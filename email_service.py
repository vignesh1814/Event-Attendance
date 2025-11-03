import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pytz import timezone

# Email Configuration - Using environment variables for security
GMAIL_USERNAME = os.environ.get('GMAIL_USERNAME', 'mr.ani30617@gmail.com')
GMAIL_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')  # Set this using environment variable (App Password)
INDIA_TZ = timezone('Asia/Kolkata')

def send_email(to_email, subject, plain_body, html_body=None):
    """Send an email using Gmail SMTP. Sends both plain text and HTML (if provided)."""
    # fast-fail if app password is not configured
    if not GMAIL_PASSWORD:
        print("Error sending email: GMAIL_APP_PASSWORD not set in environment. Aborting send.")
        return False, "GMAIL_APP_PASSWORD not set"

    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = GMAIL_USERNAME
        msg['To'] = to_email
        msg['Subject'] = subject

        # Plain text part
        part1 = MIMEText(plain_body, 'plain')
        msg.attach(part1)

        # HTML part (optional)
        if html_body:
            part2 = MIMEText(html_body, 'html')
            msg.attach(part2)

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.ehlo()
        server.starttls()
        server.login(GMAIL_USERNAME, GMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"Email sent successfully to {to_email}")
        return True, None
    except Exception as e:
        print(f"Error sending email: {e}")
        return False, str(e)

def get_unsent_attendance_for_hod(conn, hod_email, email_time):
    """Get attendance records not yet emailed for a specific HOD and time slot"""
    # Get HOD's branch
    hod = conn.execute("SELECT branch FROM users WHERE email=? AND role='hod'", (hod_email,)).fetchone()
    if not hod:
        return None, []
        
    # Get events and attendance for students in HOD's branch
    # Sort by student roll so emails list students in roll-number order
    rows = conn.execute("""
        SELECT e.title, e.description, a.id as attendance_id, 
               s.roll, s.name as student_name, a.scanned_at, a.status
        FROM attendance a
        JOIN events e ON a.event_id = e.id
        JOIN students s ON a.roll = s.roll
        LEFT JOIN sent_emails se ON a.id = se.attendance_id AND se.email_time = ?
        WHERE s.branch = ? AND se.id IS NULL
        ORDER BY s.roll ASC, a.scanned_at ASC
    """, (email_time, hod["branch"])).fetchall()
    
    return hod["branch"], rows

def format_attendance_report(branch, rows):
    """Return a tuple (plain_text, html) for the attendance report grouped by event.

    The HTML is a clean table for readability in mail clients.
    """
    if not rows:
        return ("No new attendance records to report.", "<p>No new attendance records to report.</p>")

    plain_lines = [f"Attendance Report for department: {branch}", ""]
    html_parts = [f"<h2>Attendance Report for department: {branch}</h2>"]

    current_event = None
    table_rows = []

    for row in rows:
        if current_event != row["title"]:
            # flush previous table if any
            if table_rows:
                # append table HTML
                html_parts.append("<table border=1 cellpadding=6 cellspacing=0 style='border-collapse:collapse;width:100%;margin-bottom:18px;'>")
                html_parts.append("<thead><tr><th>Roll</th><th>Name</th><th>Status</th><th>Scanned At (UTC)</th></tr></thead>")
                html_parts.append("<tbody>")
                html_parts.extend(table_rows)
                html_parts.append("</tbody></table>")
                table_rows = []

            current_event = row["title"]
            plain_lines.append(f"Event: {row['title']}")
            plain_lines.append(f"Description: {row['description']}")
            plain_lines.append("Student details:")

            html_parts.append(f"<h3 style='margin-bottom:6px;'>Event: {row['title']}</h3>")
            html_parts.append(f"<div style='margin-bottom:8px;color:#555;'>{row['description']}</div>")

        plain_lines.append(f"- {row['roll']} - {row['student_name']} (Status: {row['status']}, Scanned: {row['scanned_at']})")
        # prepare HTML row
        safe_name = row['student_name'] or 'Unknown'
        table_rows.append(f"<tr><td style='padding:6px'>{row['roll']}</td><td style='padding:6px'>{safe_name}</td><td style='padding:6px'>{row['status']}</td><td style='padding:6px'>{row['scanned_at']}</td></tr>")

    # flush last table
    if table_rows:
        html_parts.append("<table border=1 cellpadding=6 cellspacing=0 style='border-collapse:collapse;width:100%;margin-bottom:18px;'>")
        html_parts.append("<thead><tr><th>Roll</th><th>Name</th><th>Status</th><th>Scanned At (UTC)</th></tr></thead>")
        html_parts.append("<tbody>")
        html_parts.extend(table_rows)
        html_parts.append("</tbody></table>")

    plain_body = "\n".join(plain_lines)
    html_body = "".join(html_parts)
    return (plain_body, html_body)

def mark_attendance_as_sent(conn, attendance_ids, email_time):
    """Mark attendance records as emailed"""
    if not attendance_ids:
        return
        
    now = datetime.utcnow().isoformat()
    for att_id in attendance_ids:
        conn.execute(
            "INSERT OR IGNORE INTO sent_emails (attendance_id, sent_at, email_time) VALUES (?, ?, ?)",
            (att_id, now, email_time)
        )
    conn.commit()

def send_hod_attendance_reports(conn, email_time):
    """Send attendance reports to all HODs for a specific time slot"""
    hod_emails = [row["email"] for row in 
                 conn.execute("SELECT email FROM users WHERE role='hod'").fetchall()]
    
    for hod_email in hod_emails:
        branch, rows = get_unsent_attendance_for_hod(conn, hod_email, email_time)
        if not branch:
            print(f"Skipping {hod_email}: no HOD record found or branch missing")
            continue

        if not rows:
            print(f"No new attendance to send to {hod_email} (branch={branch}) for slot {email_time}")
            continue

        now = datetime.now(INDIA_TZ)
        subject = f"Event attendance for students on {now.strftime('%Y-%m-%d')} at {email_time} for department: {branch}"
        plain_body, html_body = format_attendance_report(branch, rows)

        print(f"Preparing to send {len(rows)} records to {hod_email} (branch={branch}) for slot {email_time}")
        sent, reason = send_email(hod_email, subject, plain_body, html_body)
        if sent:
            attendance_ids = [row["attendance_id"] for row in rows]
            mark_attendance_as_sent(conn, attendance_ids, email_time)
            print(f"Marked {len(attendance_ids)} attendance rows as sent for {hod_email}")
        else:
            print(f"Failed to send email to {hod_email}. Reason: {reason}")