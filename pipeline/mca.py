"""Read MCA monthly incorporation workbooks."""
import re

import openpyxl

CIN_RE = re.compile(r"^([LU])(\d{5})([A-Z]{2})(\d{4})([A-Z]{3})(\d{6})$")

STATES = {"AN": "Andaman & Nicobar", "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh", "AS": "Assam", "BR": "Bihar",
          "CH": "Chandigarh", "CT": "Chhattisgarh", "DL": "Delhi", "DN": "Dadra & Nagar Haveli", "GA": "Goa",
          "GJ": "Gujarat", "HP": "Himachal Pradesh", "HR": "Haryana", "JH": "Jharkhand", "JK": "Jammu & Kashmir",
          "KA": "Karnataka", "KL": "Kerala", "LA": "Ladakh", "MH": "Maharashtra", "ML": "Meghalaya", "MN": "Manipur",
          "MP": "Madhya Pradesh", "MZ": "Mizoram", "NL": "Nagaland", "OR": "Odisha", "PB": "Punjab", "PY": "Puducherry",
          "RJ": "Rajasthan", "SK": "Sikkim", "TG": "Telangana", "TS": "Telangana", "TN": "Tamil Nadu", "TR": "Tripura",
          "UP": "Uttar Pradesh", "UR": "Uttarakhand", "WB": "West Bengal", "PN": "Maharashtra (Pune)",
          "TZ": "Tamil Nadu (Coimbatore)", "DC": "Delhi", "UW": "Uttarakhand"}


def read_report(path):
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb["Indian Companies"] if "Indian Companies" in wb.sheetnames else wb.worksheets[0]
    header, rows = None, []
    for row in ws.iter_rows(values_only=True):
        cells = [str(v).strip() if v is not None else "" for v in row]
        if header is None:
            if any(c.upper() == "CIN" for c in cells):
                header = [c.lower() for c in cells]
            continue
        if any(cells):
            rows.append(dict(zip(header, cells)))
    return rows
