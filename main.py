import pandas as pd
import os
import smtplib
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from apscheduler.schedulers.background import BackgroundScheduler

# =========================
# FASTAPI APP
# =========================

app = FastAPI(title="Loan Risk Monitoring API")

# =========================
# CONFIG
# =========================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

# =========================
# SAFE CSV LOADER
# =========================

def load_csv(filename):

    path = os.path.join(BASE_DIR, filename)

    if not os.path.exists(path):
        print(f"{filename} not found")
        return pd.DataFrame()

    df = pd.read_csv(path)
    df.columns = df.columns.str.strip().str.lower()
    return df

# =========================
# LOAD CSV FILES
# =========================

agreement = load_csv("agreement_details.csv")
product = load_csv("product_details.csv")
dealer = load_csv("dealer_details.csv")
employee = load_csv("employee_details.csv")
bounce = load_csv("bounce_details.csv")
payment = load_csv("payment_details.csv")

print("CSV files loaded")

# =========================
# REQUEST MODEL
# =========================

class AgreementQuery(BaseModel):
    agreement_no: int

# =========================
# SAFE MERGE
# =========================

def safe_merge(left_df, right_df, left_key, right_key):

    if left_key not in left_df.columns or right_key not in right_df.columns:
        return left_df

    return left_df.merge(
        right_df,
        left_on=left_key,
        right_on=right_key,
        how="left"
    )

# =========================
# ROOT API
# =========================

@app.get("/")
def home():

    return {
        "service": "Loan Risk Monitoring API",
        "status": "running"
    }

# =========================
# HEALTH CHECK
# =========================

@app.get("/health")
def health():

    return {"status": "ok"}

# =========================
# MASTER API
# =========================

@app.post("/get_master")
def get_master(query: AgreementQuery):

    if "agreement_no" not in agreement.columns:
        return {"error": "agreement_no column missing"}

    a = agreement[agreement["agreement_no"] == query.agreement_no]

    if a.empty:
        return JSONResponse(
            status_code=404,
            content={"error": "Agreement not found"}
        )

    m = a.copy()

    m = safe_merge(m, product, "product_id", "product_id")
    m = safe_merge(m, dealer, "dealer_id", "dealer_id")
    m = safe_merge(m, employee, "employee_id", "employee_id")

    return m.to_dict(orient="records")[0]

# =========================
# BOUNCE API
# =========================

@app.post("/get_bounce")
def get_bounce(query: AgreementQuery):

    if "agreement_no" not in bounce.columns:
        return {"agreement_no": query.agreement_no, "bounce_count": 0}

    count = len(bounce[bounce["agreement_no"] == query.agreement_no])

    return {
        "agreement_no": query.agreement_no,
        "bounce_count": int(count)
    }

# =========================
# DPD API
# =========================

@app.post("/get_dpd")
def get_dpd(query: AgreementQuery):

    if "agreement_no" not in payment.columns:
        return {"agreement_no": query.agreement_no, "dpd": 0}

    p = payment[payment["agreement_no"] == query.agreement_no]

    if p.empty:
        return {"agreement_no": query.agreement_no, "dpd": 0}

    row = p.iloc[0]

    due = pd.to_datetime(row.get("due_date"), errors="coerce")
    paid = pd.to_datetime(row.get("payment_date"), errors="coerce")

    if pd.isna(due) or pd.isna(paid):
        dpd = 0
    else:
        dpd = (paid - due).days

    return {
        "agreement_no": query.agreement_no,
        "dpd": int(dpd)
    }

# =========================
# EMAIL FUNCTION
# =========================

def send_via_gmail(body, csv_path=None):

    if not EMAIL_USER or not EMAIL_PASS or not EMAIL_TO:
        print("Email credentials not configured")
        return

    msg = MIMEMultipart()

    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg["Subject"] = "Daily Risk Alert"

    msg.attach(MIMEText(body, "plain"))

    if csv_path and os.path.exists(csv_path):

        with open(csv_path, "rb") as f:

            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())

            encoders.encode_base64(part)

            part.add_header(
                "Content-Disposition",
                f"attachment; filename={os.path.basename(csv_path)}"
            )

            msg.attach(part)

    try:

        with smtplib.SMTP("smtp.gmail.com", 587) as server:

            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)

        print("Email sent")

    except Exception as e:

        print("Email error:", e)

# =========================
# RISK ENGINE
# =========================

def run_risk_analysis():

    print("Running Risk Analysis...")

    results = []

    if "agreement_no" not in agreement.columns:
        return {"error": "agreement_no column missing"}

    for ag in agreement["agreement_no"]:

        bounce_count = 0
        dpd = 0

        if "agreement_no" in bounce.columns:
            bounce_count = len(bounce[bounce["agreement_no"] == ag])

        if "agreement_no" in payment.columns:

            p = payment[payment["agreement_no"] == ag]

            if not p.empty:

                row = p.iloc[0]

                due = pd.to_datetime(row.get("due_date"), errors="coerce")
                paid = pd.to_datetime(row.get("payment_date"), errors="coerce")

                if not pd.isna(due) and not pd.isna(paid):
                    dpd = (paid - due).days

        if dpd > 10 or bounce_count >= 2:

            if dpd > 30:
                risk = "HIGH RISK"
                action = "Legal Notice Triggered"
            else:
                risk = "MEDIUM RISK"
                action = "Reminder Mail Triggered"

            results.append({
                "agreement_no": ag,
                "DPD": dpd,
                "Bounce": bounce_count,
                "Risk": risk,
                "Action": action
            })

    output_path = os.path.join(BASE_DIR, "daily_risk_output.csv")

    if results:

        df = pd.DataFrame(results)
        df.to_csv(output_path, index=False)

        body = f"""Daily Risk Report

Date: {pd.Timestamp.now()}

Total Risky Agreements: {len(results)}
"""

        send_via_gmail(body, output_path)

    return {"risky_agreements": len(results)}

# =========================
# RUN RISK API
# =========================

@app.get("/run-risk")
def trigger_risk():

    result = run_risk_analysis()

    return {
        "message": "Risk analysis completed",
        "result": result
    }

# =========================
# SCHEDULER (AUTO RUN 11:50 AM)
# =========================

scheduler = BackgroundScheduler()

def scheduled_risk_job():
    print("Scheduled job running (11:50 AM)")
    run_risk_analysis()

scheduler.add_job(
    scheduled_risk_job,
    'cron',
    hour=11,
    minute=50
)

scheduler.start()
