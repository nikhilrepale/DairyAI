# ICICI payment workbook integration (DairyAI)

This version replaces the earlier generic NEFT Excel with the **ICICI PAB_VENDOR 19-column sample layout** supplied by the dairy operator. **The source workbook included confidential beneficiary data and is intentionally not redistributed or checked into the project.**

## Workflow

1. Add the dairy's debit bank account in **Settings** (encrypted at rest).
2. Add each farmer's bank account in **Farmers**. Alphanumeric accounts and leading zeros are preserved. Select **NEFT**, **FT**, or **RTGS** for that farmer. **Don't infer FT solely from an ICICI IFSC:** the source workbook contains both FT and NEFT for ICICI beneficiaries.
3. Finalize the term in **Billing**. The exported amount comes from the finalized bill's `payout` snapshot, not from a frontend calculation.
4. Click **Bank Excel**, review totals/missing accounts, select the **payment date** (which can differ from the term-end date), then download the ICICI-format XLSX.
5. Review the entire file, check against the finalized statement, and confirm the upload-format validity with the bank before submitting.

The export never initiates a bank transfer or marks a farmer as paid. Repeated downloads are audited and warned about; bank acknowledgements must be reconciled manually.

## Sheet and column order

The output has a populated `Sheet1` and empty `Sheet2` to align with the sample workbook. Every exported row contains these **19 columns in exactly this order**:

| No. | Column | Source |
|---:|---|---|
| 1 | PYMT_PROD_TYPE_CODE | Constant `PAB_VENDOR` (from sample) |
| 2 | PYMT_MODE | Farmer bank configuration: `NEFT`, `FT`, or `RTGS` (default `NEFT`) |
| 3 | DEBIT_ACC_NO | Dairy's encrypted debit account |
| 4 | BNF_NAME | Farmer beneficiary account-holder name |
| 5 | BENE_ACC_NO | Farmer's encrypted bank account (kept as text) |
| 6 | BENE_IFSC | Beneficiary IFSC |
| 7 | AMOUNT | Finalized positive payout, two decimal places, stored as text to match sample |
| 8 | DEBIT_NARR | Space, as in sample |
| 9 | CREDIT_NARR | Space, as in sample |
| 10 | MOBILE_NUM | Farmer's existing 10-digit contact number or blank |
| 11 | EMAIL_ID | Blank (not currently stored for farmers) |
| 12 | REMARK | Blank |
| 13 | PYMT_DATE | Date chosen during export, `DD-MM-YYYY` |
| 14 | REF_NO | Blank |
| 15–19 | ADDL_INFO1–5 | Blank |

The provided example used 52 beneficiary rows, mainly `NEFT` with two `FT` rows. It used the same date on every row. The system exports the **actual number of eligible farmers**, not exactly 52.

## Validation and confidentiality

- Only a dairy owner can create and download an export.
- Only finalized billing periods can be exported.
- Farmer and dairy account numbers are encrypted with `BANK_DATA_KEY`, never returned unmasked to the browser, and never written to logs.
- Accounts may include A–Z letters because the supplied sample includes such values. Account numbers remain text so leading zeros are not dropped.
- Positive-payable farmers with missing bank details block a full-batch export; nothing is silently omitted.
- A file-download audit record stores count, amount, checksum and export date but is **not** proof of bank submission or payment.

This is a structural match to a **user-supplied sample**, not proof of bank acceptance or current ICICI specifications. Before real payments, validate against a newly downloaded official bank import template and run a bank-sanctioned test batch.
