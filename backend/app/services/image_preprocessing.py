from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps


_CELL_FIELDS = ("customer_number", "litres", "fat_percentage", "snf")


class ImagePreprocessingError(ValueError):
    pass


@dataclass(frozen=True)
class CellCrop:
    table_index: int
    row_index: int
    column_index: int
    field: str
    coordinates: dict[str, int]
    processed_box: tuple[int, int, int, int]
    image: bytes
    original_image: bytes
    is_empty: bool


@dataclass(frozen=True)
class ImagePreprocessingResult:
    original_image: bytes
    processed_image: bytes
    table_regions: list[dict[str, int]]
    cells: list[CellCrop]
    debug_images: dict[str, bytes]
    warnings: list[str]

    def layout(self) -> dict[str, Any]:
        return {
            "coordinate_space": "original_image_pixels",
            "table_regions": self.table_regions,
            "cells": [
                {
                    "table_index": cell.table_index,
                    "row_index": cell.row_index,
                    "column_index": cell.column_index,
                    "field": cell.field,
                    "coordinates": cell.coordinates,
                    "is_empty": cell.is_empty,
                }
                for cell in self.cells
            ],
            "warnings": self.warnings,
        }


class ImagePreprocessingService:
    """Normalize register images and locate cell crops in original-image coordinates."""

    def __init__(self, debug_dir: str | Path | None = None):
        self.debug_dir = Path(debug_dir) if debug_dir else None

    def process(self, image_bytes: bytes) -> ImagePreprocessingResult:
        original = self._decode(image_bytes)
        height, width = original.shape[:2]
        transform = np.eye(3, dtype=np.float64)

        page_quad = self._paper_boundary(original)
        warped, perspective = self._perspective_correct(original, page_quad)
        transform = perspective @ transform

        deskewed, rotation = self._deskew(warped)
        transform = rotation @ transform

        enhanced = self._enhance(deskewed)
        processed_bytes = self._encode_png(enhanced)

        region_boxes = self._detect_table_regions(enhanced)
        inverse_transform = np.linalg.inv(transform)
        tables: list[dict[str, int]] = []
        cells: list[CellCrop] = []
        warnings: list[str] = []
        boundary_debug = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)

        for table_index, (x, y, w, h) in enumerate(region_boxes):
            tables.append(self._original_coordinates(x, y, w, h, inverse_transform, width, height))
            cv2.rectangle(boundary_debug, (x, y), (x + w, y + h), (0, 0, 255), 3)
            table_cells = self._extract_table_cells(
                enhanced, original, (x, y, w, h), table_index,
                inverse_transform, width, height,
            )
            if table_cells is None:
                warnings.append(f"Could not reliably locate grid lines in table {table_index + 1}.")
                continue
            cells.extend(table_cells)
            for cell in table_cells:
                bx, by, bw, bh = cell.processed_box
                cv2.rectangle(boundary_debug, (bx, by), (bx + bw, by + bh), (0, 180, 0), 1)

        if not region_boxes:
            warnings.append("No table regions with detectable grid lines were found.")

        debug_images = {
            "detected_boundaries.png": self._encode_png(boundary_debug),
            "cell_crops.png": self._make_crop_montage(cells),
        }
        if self.debug_dir:
            self._write_debug_images(debug_images, cells)

        return ImagePreprocessingResult(
            original_image=image_bytes,
            processed_image=processed_bytes,
            table_regions=tables,
            cells=cells,
            debug_images=debug_images,
            warnings=warnings,
        )

    @staticmethod
    def _decode(image_bytes: bytes) -> np.ndarray:
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                if image.format not in {"JPEG", "PNG"}:
                    raise ImagePreprocessingError("Only JPG, JPEG, and PNG images are supported.")
                image = ImageOps.exif_transpose(image).convert("RGB")
                return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
        except OSError as exc:
            raise ImagePreprocessingError("Uploaded file is not a valid JPG or PNG image.") from exc

    @staticmethod
    def _paper_boundary(image: np.ndarray) -> np.ndarray:
        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 40, 130)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        candidates: list[tuple[float, np.ndarray]] = []
        image_area = float(width * height)
        for contour in contours:
            perimeter = cv2.arcLength(contour, True)
            polygon = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
            area = abs(cv2.contourArea(polygon))
            if len(polygon) == 4 and cv2.isContourConvex(polygon) and area >= image_area * 0.2:
                candidates.append((area, polygon.reshape(4, 2).astype(np.float32)))
        if not candidates:
            return np.array(
                [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
                dtype=np.float32,
            )
        return ImagePreprocessingService._order_quad(max(candidates, key=lambda item: item[0])[1])

    @staticmethod
    def _order_quad(points: np.ndarray) -> np.ndarray:
        ordered = np.zeros((4, 2), dtype=np.float32)
        sums = points.sum(axis=1)
        differences = np.diff(points, axis=1).reshape(-1)
        ordered[0] = points[np.argmin(sums)]
        ordered[2] = points[np.argmax(sums)]
        ordered[1] = points[np.argmin(differences)]
        ordered[3] = points[np.argmax(differences)]
        return ordered

    @staticmethod
    def _perspective_correct(image: np.ndarray, quad: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        top_left, top_right, bottom_right, bottom_left = quad
        out_width = max(
            1,
            int(max(np.linalg.norm(bottom_right - bottom_left), np.linalg.norm(top_right - top_left))),
        )
        out_height = max(
            1,
            int(max(np.linalg.norm(top_right - bottom_right), np.linalg.norm(top_left - bottom_left))),
        )
        target = np.array(
            [[0, 0], [out_width - 1, 0], [out_width - 1, out_height - 1], [0, out_height - 1]],
            dtype=np.float32,
        )
        transform = cv2.getPerspectiveTransform(quad, target)
        warped = cv2.warpPerspective(image, transform, (out_width, out_height), borderValue=(255, 255, 255))
        return warped, transform

    @staticmethod
    def _deskew(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        lines = cv2.HoughLinesP(
            edges, 1, np.pi / 1800, threshold=max(30, width // 8),
            minLineLength=max(30, width // 10), maxLineGap=max(8, width // 100),
        )
        if lines is None:
            return image, np.eye(3, dtype=np.float64)
        line_segments = np.asarray(lines).reshape(-1, 4)
        angles = []
        for x1, y1, x2, y2 in line_segments:
            angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
            if abs(angle) <= 15:
                angles.append(angle)
        if not angles:
            return image, np.eye(3, dtype=np.float64)
        skew = float(np.median(angles))
        if abs(skew) < 0.15:
            return image, np.eye(3, dtype=np.float64)

        center = (width / 2.0, height / 2.0)
        affine = cv2.getRotationMatrix2D(center, skew, 1.0)
        cos_a, sin_a = abs(affine[0, 0]), abs(affine[0, 1])
        out_width = int(height * sin_a + width * cos_a)
        out_height = int(height * cos_a + width * sin_a)
        affine[0, 2] += out_width / 2.0 - center[0]
        affine[1, 2] += out_height / 2.0 - center[1]
        rotated = cv2.warpAffine(
            image, affine, (out_width, out_height), borderValue=(255, 255, 255)
        )
        transform = np.vstack((affine, [0.0, 0.0, 1.0]))
        return rotated, transform

    @staticmethod
    def _enhance(image: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        denoised = cv2.fastNlMeansDenoising(gray, None, h=8, templateWindowSize=7, searchWindowSize=21)
        return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(denoised)

    @staticmethod
    def _detect_table_regions(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
        height, width = gray.shape
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 12
        )
        horizontal = cv2.morphologyEx(
            binary, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (max(12, width // 35), 1)),
        )
        vertical = cv2.morphologyEx(
            binary, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, height // 35))),
        )
        grid = cv2.dilate(cv2.add(horizontal, vertical), np.ones((3, 3), dtype=np.uint8))
        contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        regions: list[tuple[int, int, int, int]] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w >= width * 0.16 and h >= height * 0.12 and w * h >= width * height * 0.018:
                regions.append((x, y, w, h))
        regions.sort(key=lambda rect: rect[0])

        # A connected grid mask can join both tables; split only at a clear central gutter.
        if len(regions) == 1 and regions[0][2] >= width * 0.58:
            x, y, w, h = regions[0]
            local = vertical[:, x:x + w]
            projection = local.sum(axis=0)
            lower, upper = int(w * 0.35), int(w * 0.65)
            if upper > lower:
                split = lower + int(np.argmin(projection[lower:upper]))
                if projection[split] < max(255 * 2, int(projection.max() * 0.2)):
                    regions = [(x, y, split, h), (x + split, y, w - split, h)]
        return regions

    def _extract_table_cells(
        self,
        gray: np.ndarray,
        original: np.ndarray,
        region: tuple[int, int, int, int],
        table_index: int,
        inverse_transform: np.ndarray,
        original_width: int,
        original_height: int,
    ) -> list[CellCrop] | None:
        x, y, w, h = region
        table = gray[y:y + h, x:x + w]
        binary = cv2.adaptiveThreshold(
            table, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 12
        )
        horizontal = cv2.morphologyEx(
            binary, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, w // 25), 1)),
        )
        vertical = cv2.morphologyEx(
            binary, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, h // 25))),
        )
        x_lines = self._line_centers(vertical.sum(axis=0), minimum_run=2)
        y_lines = self._line_centers(horizontal.sum(axis=1), minimum_run=2)
        x_lines = self._with_edges(x_lines, w)
        y_lines = self._with_edges(y_lines, h)
        if len(x_lines) < 5 or len(y_lines) < 2:
            return None

        # The register's four fields are its four printed columns; ignore extra detected marks.
        if len(x_lines) > 5:
            x_lines = self._select_column_boundaries(x_lines, 5, vertical.sum(axis=0))
        if len(x_lines) != 5:
            return None
        cell_crops: list[CellCrop] = []
        for row_index, (y0, y1) in enumerate(zip(y_lines, y_lines[1:])):
            if y1 - y0 < 5:
                continue
            for column_index, (x0, x1) in enumerate(zip(x_lines, x_lines[1:5])):
                if x1 - x0 < 5:
                    continue
                inset = 2
                left, top = x + x0 + inset, y + y0 + inset
                right, bottom = x + x1 - inset, y + y1 - inset
                if right <= left or bottom <= top:
                    continue
                crop = gray[top:bottom, left:right]
                coordinates = self._original_coordinates(
                    left, top, right - left, bottom - top, inverse_transform,
                    original_width, original_height,
                )
                source_x, source_y = coordinates["x"], coordinates["y"]
                source_crop = original[
                    source_y:source_y + coordinates["height"],
                    source_x:source_x + coordinates["width"],
                ]
                cell_crops.append(
                    CellCrop(
                        table_index=table_index,
                        row_index=row_index,
                        column_index=column_index,
                        field=_CELL_FIELDS[column_index],
                        coordinates=coordinates,
                        processed_box=(left, top, right - left, bottom - top),
                        image=self._encode_png(crop),
                        original_image=self._encode_png(source_crop),
                        is_empty=self._is_empty(crop),
                    )
                )
        return cell_crops

    @staticmethod
    def _line_centers(projection: np.ndarray, minimum_run: int) -> list[int]:
        if projection.size == 0 or int(projection.max()) == 0:
            return []
        threshold = max(255 * 2, int(projection.max() * 0.2))
        active = projection >= threshold
        positions = np.flatnonzero(active)
        if not positions.size:
            return []
        groups = np.split(positions, np.where(np.diff(positions) > 2)[0] + 1)
        return [
            int(round(float(group.mean())))
            for group in groups
            if len(group) >= minimum_run
        ]

    @staticmethod
    def _with_edges(lines: list[int], extent: int) -> list[int]:
        result = sorted(set(lines))
        if not result or result[0] > max(3, extent * 0.03):
            result.insert(0, 0)
        else:
            result[0] = 0
        if extent - 1 - result[-1] > max(3, extent * 0.03):
            result.append(extent - 1)
        else:
            result[-1] = extent - 1
        return result

    @staticmethod
    def _select_column_boundaries(lines: list[int], count: int, projection: np.ndarray) -> list[int]:
        if len(lines) <= count:
            return lines
        endpoints = [lines[0], lines[-1]]
        interior = sorted(lines[1:-1], key=lambda line: int(projection[line]), reverse=True)
        return sorted(endpoints + interior[:count - 2])

    @staticmethod
    def _is_empty(crop: np.ndarray) -> bool:
        if crop.size == 0:
            return True
        margin_y = max(1, int(crop.shape[0] * 0.08))
        margin_x = max(1, int(crop.shape[1] * 0.08))
        inner = crop[margin_y:-margin_y, margin_x:-margin_x]
        if inner.size == 0:
            inner = crop
        foreground = int(np.count_nonzero(inner < 200))
        return foreground < max(8, int(inner.size * 0.003))

    @staticmethod
    def _original_coordinates(
        x: int,
        y: int,
        width: int,
        height: int,
        inverse_transform: np.ndarray,
        original_width: int,
        original_height: int,
    ) -> dict[str, int]:
        corners = np.array(
            [[[x, y], [x + width, y], [x + width, y + height], [x, y + height]]],
            dtype=np.float64,
        )
        mapped = cv2.perspectiveTransform(corners, inverse_transform)[0]
        left = max(0, int(np.floor(mapped[:, 0].min())))
        top = max(0, int(np.floor(mapped[:, 1].min())))
        right = min(original_width, int(np.ceil(mapped[:, 0].max())))
        bottom = min(original_height, int(np.ceil(mapped[:, 1].max())))
        return {"x": left, "y": top, "width": max(0, right - left), "height": max(0, bottom - top)}

    def _make_crop_montage(self, cells: list[CellCrop]) -> bytes:
        if not cells:
            empty = np.full((80, 320), 255, dtype=np.uint8)
            return self._encode_png(empty)
        tiles: list[np.ndarray] = []
        for cell in cells[:160]:
            with Image.open(io.BytesIO(cell.image)) as image:
                tile = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
            tile = cv2.resize(tile, (160, 72))
            cv2.putText(
                tile, f"T{cell.table_index + 1} R{cell.row_index + 1} {cell.field[:8]}",
                (3, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 0, 255), 1,
            )
            tiles.append(tile)
        columns = 4
        rows = (len(tiles) + columns - 1) // columns
        montage = np.full((rows * 72, columns * 160, 3), 255, dtype=np.uint8)
        for index, tile in enumerate(tiles):
            row, column = divmod(index, columns)
            montage[row * 72:(row + 1) * 72, column * 160:(column + 1) * 160] = tile
        return self._encode_png(montage)

    def _write_debug_images(self, images: dict[str, bytes], cells: list[CellCrop]) -> None:
        assert self.debug_dir is not None
        run_dir = self.debug_dir / uuid.uuid4().hex
        run_dir.mkdir(parents=True, exist_ok=True)
        for filename, image_bytes in images.items():
            (run_dir / filename).write_bytes(image_bytes)
        crop_dir = run_dir / "cells"
        crop_dir.mkdir(exist_ok=True)
        for cell in cells:
            filename = (
                f"table-{cell.table_index + 1}-row-{cell.row_index + 1}-"
                f"column-{cell.column_index + 1}.png"
            )
            (crop_dir / filename).write_bytes(cell.image)

    @staticmethod
    def _encode_png(image: np.ndarray) -> bytes:
        success, encoded = cv2.imencode(".png", image)
        if not success:
            raise RuntimeError("Unable to encode preprocessed image.")
        return encoded.tobytes()
