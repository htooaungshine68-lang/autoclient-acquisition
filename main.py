import os
import smtplib
import ssl
import pytz
import logging
import datetime
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
from notion_client import Client

# -------------------- SETUP --------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
load_dotenv()

# -------------------- HELPERS --------------------
def safe_select(props, key):
    data = props.get(key)
    if data and data.get("select"):
        return data["select"].get("name", "")
    return ""

def safe_title(props, key):
    data = props.get(key)
    if data and data.get("title"):
        return data["title"][0].get("plain_text", "")
    return ""

def safe_rich_text(props, key):
    data = props.get(key)
    if data and data.get("rich_text"):
        return "".join(rt.get("plain_text", "") for rt in data["rich_text"])
    return ""

def safe_email(props, key):
    data = props.get(key)
    return data.get("email") if data else None

# -------------------- MAIN CLASS --------------------
class EasyColdEmail:
    def __init__(self):
        self.notion = Client(auth=os.getenv("NOTION_API_KEY"))
        self.db_id = os.getenv("NOTION_DATABASE_ID")
        self.sender_email = os.getenv("EMAIL_USER")
        self.app_password = os.getenv("EMAIL_PASS")

        self.max_daily = 30
        self.counter_file = "daily_counter.json"
        self.work_start = "09:00"
        self.work_end = "17:00"

        logging.info(f"DEBUG NOTION KEY: {os.getenv('NOTION_API_KEY')[:10]}")
        logging.info(f"DEBUG DB ID: {self.db_id[:10]}")
        logging.info(f"DEBUG EMAIL USER SET: {bool(self.sender_email)}")
        logging.info(f"DEBUG EMAIL PASS SET: {bool(self.app_password)}")

    # -------------------- TIME RULE --------------------
   def is_business_hours(self, timezone_name):
    try:
        tz = pytz.timezone(timezone_name)
    except Exception:
        logging.warning(f"Invalid timezone: {timezone_name}, defaulting to UTC")
        tz = pytz.UTC

    now = datetime.datetime.now(pytz.UTC).astimezone(tz)

    start_h, start_m = map(int, self.work_start.split(":"))
    end_h, end_m = map(int, self.work_end.split(":"))

    start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

    return start <= now <= end

    # -------------------- EMAIL --------------------
    def send_email(self, to_email, subject, html):
        msg = MIMEMultipart("alternative")
        msg["From"] = self.sender_email
        msg["To"] = to_email
        msg["Subject"] = subject

        msg.attach(MIMEText(html, "html"))

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(self.sender_email, self.app_password)
            server.send_message(msg)

    # -------------------- RUN --------------------
    def run(self):
        logging.info("🔄 Syncing with Notion...")
        pages = self.notion.databases.query(database_id=self.db_id)["results"]
        logging.info(f"📄 Found {len(pages)} rows")

        with open("email_template.html", "r", encoding="utf-8") as f:
            template = f.read()

        for page in pages:
            props = page["properties"]

            status = safe_select(props, "STATUS")
            timezone_name = safe_select(props, "Time Zone") or "UTC"
            
            if status in ["Sent", "Failed"]:
                continue

            email = safe_email(props, "EMAIL")
            if not email:
                logging.info(f"Skipping {safe_title(props, 'NAME')}: no email")
                continue

            # ---------- NAME FALLBACK ----------
            contact = safe_rich_text(props, "CONTACT PERSON")
            brand = safe_title(props, "NAME")
            name = contact or brand or "there"

            # ---------- SUBJECT FALLBACK ----------
            subject_text = safe_rich_text(props, "SUBJECT")
            subject = subject_text if subject_text else f"Hi {name}"

            # ---------- BODY FALLBACK ----------
            body = safe_rich_text(props, "BODY")

            html = (
                template
                .replace("{{name}}", name)
                .replace("{{body}}", body)
            )

            try:
                self.send_email(email, subject, html)
                if not self.is_business_hours(timezone_name):
                    logging.info(f"⏳ Skipping {email} - outside business hours in {timezone_name}")
                    continue
                self.notion.pages.update(
                    page_id=page["id"],
                    properties={
                        "STATUS": {"select": {"name": "Sent"}},
                        "SENT TIME": {"date": {"start": datetime.datetime.utcnow().isoformat()}}
                    }
                )

                logging.info(f"✅ Sent to {email}")

            except Exception as e:
                logging.error(f"❌ Failed sending to {email}: {e}")
                self.notion.pages.update(
                    page_id=page["id"],
                    properties={"STATUS": {"select": {"name": "Failed"}}}
                )

# -------------------- ENTRY --------------------
if __name__ == "__main__":
    EasyColdEmail().run()
