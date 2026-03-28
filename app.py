from flask import Flask, render_template, request, redirect, jsonify
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
import json
from model_logic import full_system
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from flask import send_file
import io

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# -------------------------
# MODELS
# -------------------------

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String, unique=True)
    student_name = db.Column(db.String)


class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String)
    review_text = db.Column(db.Text)
    submitted = db.Column(db.Boolean, default=False)


class Performance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String)
    cgpa = db.Column(db.Float)
    assignment1 = db.Column(db.Float)
    assignment2 = db.Column(db.Float)
    quiz = db.Column(db.Float)
    extra = db.Column(db.Float)
    attendance = db.Column(db.Float)


# ✅ NEW MODEL (Caching analysis)
class Analysis(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String, unique=True)

    sentiment = db.Column(db.String)
    cognition = db.Column(db.Float)
    aspects = db.Column(db.Text)  # JSON stored as string


# -------------------------
# INITIAL DATA
# -------------------------

def load_students():
    if Student.query.first():
        return

    for i in range(1, 61):
        sid = f"S{str(i).zfill(3)}"
        db.session.add(Student(student_id=sid, student_name=f"Student {i}"))

    db.session.commit()


# -------------------------
# HOME
# -------------------------

@app.route("/")
def home():
    return render_template("home.html")


# -------------------------
# STUDENT PAGE
# -------------------------

@app.route("/student")
def student_page():
    return render_template("student.html")


@app.route("/validate_student/<student_id>")
def validate_student(student_id):
    student = Student.query.filter_by(student_id=student_id).first()

    if not student:
        return jsonify({"valid": False, "message": "Invalid ID"})

    if Review.query.filter_by(student_id=student_id).first():
        return jsonify({"valid": False, "message": "Already submitted"})

    return jsonify({"valid": True, "name": student.student_name})


@app.route("/submit_review", methods=["POST"])
def submit_review():
    sid = request.form["student_id"]
    review_text = request.form["review"]

    # Save review
    db.session.add(Review(
        student_id=sid,
        review_text=review_text,
        submitted=True
    ))

    # Get performance data
    perf = Performance.query.filter_by(student_id=sid).first()

    if perf:
        student_data = {
            "assignment1": perf.assignment1 / 30 * 100,
            "assignment2": perf.assignment2 / 30 * 100,
            "quiz": perf.quiz / 10 * 100,
            "presentation": perf.extra / 10 * 100,
            "attendance": perf.attendance,
            "cgpa": perf.cgpa
        }

        # 🔥 Run analysis ONLY ONCE
        analysis = full_system(review_text, student_data)

        # Save / update analysis
        existing = Analysis.query.filter_by(student_id=sid).first()

        if existing:
            existing.sentiment = analysis["observed_overall"]
            existing.cognition = analysis.get("cognition_score", 0)
            existing.aspects = json.dumps(analysis.get("aspect_analysis_with_cognition", {}))
        else:
            db.session.add(Analysis(
                student_id=sid,
                sentiment=analysis["observed_overall"],
                cognition=analysis.get("cognition_score", 0),
                aspects=json.dumps(analysis.get("aspect_analysis_with_cognition", {}))
            ))

    db.session.commit()

    return "<span style='color:green;'>Submitted successfully</span>"


# -------------------------
# TEACHER PAGE
# -------------------------

@app.route("/teacher")
def teacher():
    data = []

    students = Student.query.all()
    performance = {p.student_id: p for p in Performance.query.all()}

    for s in students:
        p = performance.get(s.student_id)

        data.append({
            "student_id": s.student_id,
            "cgpa": p.cgpa if p else "",
            "assignment1": p.assignment1 if p else "",
            "assignment2": p.assignment2 if p else "",
            "quiz": p.quiz if p else "",
            "extra": p.extra if p else "",
            "attendance": p.attendance if p else ""
        })

    return render_template("teacher.html", data=data)


@app.route("/upload_excel", methods=["POST"])
def upload_excel():
    df = pd.read_excel(request.files["file"])

    for _, row in df.iterrows():
        sid = row["student_id"]

        # -------- Save Performance --------
        p = Performance.query.filter_by(student_id=sid).first()

        if not p:
            p = Performance(student_id=sid)
            db.session.add(p)

        p.cgpa = row["cgpa"]
        p.assignment1 = row["assignment1"]
        p.assignment2 = row["assignment2"]
        p.quiz = row["quiz"]
        p.extra = row["extra"]
        p.attendance = row["attendance"]

        # -------- Recompute Analysis (IMPORTANT) --------
        review = Review.query.filter_by(student_id=sid).first()

        if review:
            student_data = {
                "assignment1": row["assignment1"] / 30 * 100,
                "assignment2": row["assignment2"] / 30 * 100,
                "quiz": row["quiz"] / 10 * 100,
                "presentation": row["extra"] / 10 * 100,
                "attendance": row["attendance"],
                "cgpa": row["cgpa"]
            }

            analysis = full_system(review.review_text, student_data)

            existing = Analysis.query.filter_by(student_id=sid).first()

            if existing:
                existing.sentiment = analysis["observed_overall"]
                existing.cognition = analysis.get("cognition_score", 0)
                existing.aspects = json.dumps(
                    analysis.get("aspect_analysis_with_cognition", {})
                )
            else:
                db.session.add(Analysis(
                    student_id=sid,
                    sentiment=analysis["observed_overall"],
                    cognition=analysis.get("cognition_score", 0),
                    aspects=json.dumps(
                        analysis.get("aspect_analysis_with_cognition", {})
                    )
                ))

    db.session.commit()

    return redirect("/teacher")


@app.route("/save_performance", methods=["POST"])
def save_performance():
    data = request.get_json()
    sid = data["student_id"]

    p = Performance.query.filter_by(student_id=sid).first()

    if p:
        p.cgpa = data["cgpa"]
        p.assignment1 = data["assignment1"]
        p.assignment2 = data["assignment2"]
        p.quiz = data["quiz"]
        p.extra = data["extra"]
        p.attendance = data["attendance"]
    else:
        db.session.add(Performance(
            student_id=sid,
            cgpa=data["cgpa"],
            assignment1=data["assignment1"],
            assignment2=data["assignment2"],
            quiz=data["quiz"],
            extra=data["extra"],
            attendance=data["attendance"]
        ))

    db.session.commit()

    # 🔥 Recompute analysis if review exists
    review = Review.query.filter_by(student_id=sid).first()
    if review:
        student_data = {
            "assignment1": data["assignment1"] / 30 * 100,
            "assignment2": data["assignment2"] / 30 * 100,
            "quiz": data["quiz"] / 10 * 100,
            "presentation": data["extra"] / 10 * 100,
            "attendance": data["attendance"],
            "cgpa": data["cgpa"]
        }

        analysis = full_system(review.review_text, student_data)

        existing = Analysis.query.filter_by(student_id=sid).first()
        if existing:
            existing.sentiment = analysis["observed_overall"]
            existing.cognition = analysis.get("cognition_score", 0)
            existing.aspects = json.dumps(analysis.get("aspect_analysis_with_cognition", {}))
        else:
            db.session.add(Analysis(
                student_id=sid,
                sentiment=analysis["observed_overall"],
                cognition=analysis.get("cognition_score", 0),
                aspects=json.dumps(analysis.get("aspect_analysis_with_cognition", {}))
            ))

        db.session.commit()

    return jsonify({"status": "saved"})


# -------------------------
# ADMIN DASHBOARD (FAST NOW ⚡)
# -------------------------

@app.route("/admin")
def admin_dashboard():
    students = Student.query.all()
    analyses = {a.student_id: a for a in Analysis.query.all()}
    reviews = {r.student_id: r for r in Review.query.all()}
    performances = {p.student_id: p for p in Performance.query.all()}

    # -------------------------
    # HELPER: CHECK PERFORMANCE
        # -------------------------
    import math

    def is_performance_valid(perf):
        print(sid, perf.cgpa, perf.assignment1)
        return perf and all(
            v is not None and not (isinstance(v, float) and math.isnan(v))
            for v in [
                perf.cgpa,
                perf.assignment1,
                perf.assignment2,
                perf.quiz,
                perf.extra,
                perf.attendance
            ]
        )

    total = len(students)
    submitted = 0
    pending = 0
    waiting = 0

    sentiments = {"Positive": 0, "Neutral": 0, "Negative": 0}
    aspects = {}

    high = 0
    medium = 0
    low = 0

    results = []

    # -------------------------
    # MAIN LOOP
    # -------------------------
    for student in students:
        sid = student.student_id

        review = reviews.get(sid)
        analysis = analyses.get(sid)
        perf = performances.get(sid)

        # ✅ SUBMITTED (STRICT CONDITION)
        if review and analysis and is_performance_valid(perf):
            submitted += 1

            # Sentiment count
            sentiments[analysis.sentiment] += 1

            # Performance category
            cognition = analysis.cognition or 0

            if cognition >= 0.78:
                high += 1
            elif cognition >= 0.55:
                medium += 1
            else:
                low += 1

            # Aspect aggregation
            aspect_data = json.loads(analysis.aspects or "{}")

            for asp, details in aspect_data.items():
                if asp not in aspects:
                    aspects[asp] = {"Positive": 0, "Neutral": 0, "Negative": 0}
                aspects[asp][details["sentiment"]] += 1

            # ✅ ONLY ANALYZED REVIEWS SHOWN
            results.append({
                "review": review.review_text,
                "overall": analysis.sentiment
            })

        # ✅ WAITING (REVIEW EXISTS BUT NOT FULLY READY)
        elif review:
            waiting += 1

        # ✅ PENDING (NO REVIEW)
        else:
            pending += 1

    # -------------------------
    # GLOBAL INSIGHT
    # -------------------------
    if sum(sentiments.values()) > 0:
        dominant_sentiment = max(sentiments, key=sentiments.get)
    else:
        dominant_sentiment = "No Data"

    # -------------------------
    # RENDER
    # -------------------------
    print("RESULTS COUNT:", len(results))
    return render_template(
        "admin.html",
        total=total,
        submitted=submitted,
        pending=pending,
        waiting=waiting,
        sentiments=sentiments,
        aspects=aspects,
        results=results,
        high=high,
        medium=medium,
        low=low,
        dominant_sentiment=dominant_sentiment
    )
# -------------------------
# GLOBAL INSIGHT
# -------------------------
def get_dominant_sentiment(sentiments):
    return max(sentiments, key=sentiments.get)


# -------------------------
# STATUS MODAL DATA
# -------------------------
@app.route("/get_students_by_status/<status>")
def get_students(status):
    students = Student.query.all()
    analyses = {a.student_id: a for a in Analysis.query.all()}
    reviews = {r.student_id: r for r in Review.query.all()}
    performances = {p.student_id: p for p in Performance.query.all()}

    def is_performance_valid(perf):
        if not perf:
            return False
        return all([
            perf.cgpa is not None,
            perf.assignment1 is not None,
            perf.assignment2 is not None,
            perf.quiz is not None,
            perf.extra is not None,
            perf.attendance is not None
        ])

    result = []

    for s in students:
        sid = s.student_id
        review = reviews.get(sid)
        analysis = analyses.get(sid)
        perf = performances.get(sid)

        if status == "submitted":
            if review and analysis and is_performance_valid(perf):
                result.append(sid)

        elif status == "waiting":
            if review and (not analysis or not is_performance_valid(perf)):
                result.append(sid)

        elif status == "pending":
            if not review:
                result.append(sid)

    return jsonify({"students": result})


# -------------------------
# SENTIMENT PAGE
# -------------------------
@app.route("/sentiment/<sent>")
def sentiment_page(sent):
    data = []
    analyses = Analysis.query.filter_by(sentiment=sent).all()

    for a in analyses:
        r = Review.query.filter_by(student_id=a.student_id).first()
        if r:
            data.append({"review": r.review_text})

    return render_template("sentiment_page.html", data=data, sentiment=sent)


# -------------------------
# PERFORMANCE INSIGHT
# -------------------------
@app.route("/performance_insight/<level>")
def performance_insight(level):
    analyses = Analysis.query.all()

    selected = []

    for a in analyses:
        c = a.cognition or 0
        if level == "high" and c >= 0.78:
            selected.append(a)
        elif level == "medium" and 0.55 <= c < 0.78:
            selected.append(a)
        elif level == "low" and c < 0.55:
            selected.append(a)

    if not selected:
        return jsonify({"majority":"None","avg_cognition":0,"interpretation":"No data"})

    sentiments = {"Positive":0,"Neutral":0,"Negative":0}
    total_cog = 0

    for s in selected:
        sentiments[s.sentiment]+=1
        total_cog += (s.cognition or 0)

    majority = max(sentiments, key=sentiments.get)
    avg_cog = round(total_cog/len(selected),2)

    interpretation = f"{level.capitalize()} performers show {majority} sentiment."

    return jsonify({
        "majority": majority,
        "avg_cognition": avg_cog,
        "interpretation": interpretation
    })


# -------------------------
# ASPECT PAGE
# -------------------------
@app.route("/aspect/<asp>")
def aspect_page(asp):
    asp = asp.replace("_", "#")
    analyses = Analysis.query.all()

    grouped = {"Positive":[],"Neutral":[],"Negative":[]}

    for a in analyses:
        aspects = json.loads(a.aspects or "{}")

        if asp in aspects:
            sentiment = aspects[asp]["sentiment"]
            r = Review.query.filter_by(student_id=a.student_id).first()
            if r:
                grouped[sentiment].append(r.review_text)

    return render_template("aspect_page.html", grouped=grouped, aspect=asp)

@app.route("/download_summary")
def download_summary():
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()
    content = []

    students = Student.query.all()
    analyses = {a.student_id: a for a in Analysis.query.all()}
    reviews = {r.student_id: r for r in Review.query.all()}
    performances = {p.student_id: p for p in Performance.query.all()}

    # -------------------------
    # HELPER
    # -------------------------
    def is_performance_valid(perf):
        if not perf:
            return False
        return all([
            perf.cgpa is not None,
            perf.assignment1 is not None,
            perf.assignment2 is not None,
            perf.quiz is not None,
            perf.extra is not None,
            perf.attendance is not None
        ])

    total = len(students)
    submitted = 0
    pending = 0
    waiting = 0

    sentiments = {"Positive": 0, "Neutral": 0, "Negative": 0}

    high = 0
    medium = 0
    low = 0

    student_rows = []

    # -------------------------
    # MAIN LOGIC (FIXED)
    # -------------------------
    for s in students:
        sid = s.student_id

        review = reviews.get(sid)
        analysis = analyses.get(sid)
        perf = performances.get(sid)

        if review and analysis and is_performance_valid(perf):
            status = "Submitted"
            submitted += 1

            sentiment = analysis.sentiment
            cognition = round((analysis.cognition or 0), 2)

            sentiments[sentiment] += 1

            if cognition >= 0.78:
                perf_cat = "High"
                high += 1
            elif cognition >= 0.55:
                perf_cat = "Medium"
                medium += 1
            else:
                perf_cat = "Low"
                low += 1

        elif review:
            status = "Waiting"
            waiting += 1
            sentiment = "-"
            cognition = "-"
            perf_cat = "-"

        else:
            status = "Pending"
            pending += 1
            sentiment = "-"
            cognition = "-"
            perf_cat = "-"

        student_rows.append({
            "id": sid,
            "status": status,
            "sentiment": sentiment,
            "cognition": cognition,
            "performance": perf_cat
        })

    # -------------------------
    # DOMINANT SENTIMENT
    # -------------------------
    dominant = max(sentiments, key=sentiments.get) if sum(sentiments.values()) else "No Data"

    # -------------------------
    # PDF CONTENT
    # -------------------------
    content.append(Paragraph("Course Summary Report", styles['Title']))
    content.append(Spacer(1, 12))

    content.append(Paragraph("Overview", styles['Heading2']))
    content.append(Paragraph(f"Total Students: {total}", styles['Normal']))
    content.append(Paragraph(f"Submitted: {submitted}", styles['Normal']))
    content.append(Paragraph(f"Pending: {pending}", styles['Normal']))
    content.append(Paragraph(f"Waiting: {waiting}", styles['Normal']))

    content.append(Spacer(1, 10))

    content.append(Paragraph("Sentiment Distribution", styles['Heading3']))
    for k, v in sentiments.items():
        content.append(Paragraph(f"{k}: {v}", styles['Normal']))

    content.append(Spacer(1, 10))

    content.append(Paragraph("Performance Distribution", styles['Heading3']))
    content.append(Paragraph(f"High: {high}", styles['Normal']))
    content.append(Paragraph(f"Medium: {medium}", styles['Normal']))
    content.append(Paragraph(f"Low: {low}", styles['Normal']))

    content.append(Spacer(1, 12))

    content.append(Paragraph(
        f"Overall Insight: Majority of students show {dominant} sentiment.",
        styles['Italic']
    ))

    content.append(Spacer(1, 15))

    # -------------------------
    # STUDENT DATA TABLE
    # -------------------------
    content.append(Paragraph("Student Summary Data", styles['Heading2']))
    content.append(Spacer(1, 10))

    content.append(Paragraph(
        "Student ID | Status",
        styles['Heading4']
    ))

    content.append(Spacer(1, 8))

    for row in student_rows:
        line = f"{row['id']} | {row['status']}"
        content.append(Paragraph(line, styles['Normal']))

    # -------------------------
    # BUILD PDF
    # -------------------------
    doc.build(content)
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name="summary_report.pdf",
        mimetype='application/pdf'
    )
@app.route("/download_detailed")
def download_detailed():
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()
    content = []

    students = Student.query.all()
    analyses = Analysis.query.all()
    reviews = Review.query.all()

    total = len(students)
    submitted = len(analyses)
    pending = total - len(reviews)
    waiting = len(reviews) - len(analyses)

    sentiments = {"Positive":0,"Neutral":0,"Negative":0}
    aspects = {}
    cognition_list = []

    high=medium=low=0

    # -------------------------
    # DATA COLLECTION
    # -------------------------
    for a in analyses:
        sentiments[a.sentiment]+=1

        c = a.cognition or 0
        cognition_list.append(c)

        if c>=0.78: high+=1
        elif c>=0.55: medium+=1
        else: low+=1

        asp_data = json.loads(a.aspects or "{}")

        for asp, val in asp_data.items():
            if asp not in aspects:
                aspects[asp] = {"Positive":0,"Neutral":0,"Negative":0}
            aspects[asp][val["sentiment"]] += 1

    dominant = max(sentiments, key=sentiments.get)
    avg_cognition = round(sum(cognition_list)/len(cognition_list),2) if cognition_list else 0

    # -------------------------
    # 1. TITLE
    # -------------------------
    content.append(Paragraph("Course Feedback Analysis Report", styles['Title']))
    content.append(Spacer(1,12))

    # -------------------------
    # 2. EXECUTIVE SUMMARY
    # -------------------------
    content.append(Paragraph("Executive Summary", styles['Heading1']))

    summary_text = f"""
    This report provides a comprehensive analysis of student feedback integrated with academic performance (cognition).
    The course has received predominantly <b>{dominant}</b> feedback.
    The average cognition level of students is <b>{avg_cognition}</b>, indicating overall academic engagement.
    """

    content.append(Paragraph(summary_text, styles['Normal']))
    content.append(Spacer(1,12))

    # -------------------------
    # 3. STUDENT STATUS
    # -------------------------
    content.append(Paragraph("Student Participation Overview", styles['Heading2']))
    content.append(Paragraph(f"Total Students: {total}", styles['Normal']))
    content.append(Paragraph(f"Submitted: {submitted}", styles['Normal']))
    content.append(Paragraph(f"Pending: {pending}", styles['Normal']))
    content.append(Paragraph(f"Waiting for Analysis: {waiting}", styles['Normal']))
    content.append(Spacer(1,12))

    # -------------------------
    # 4. SENTIMENT ANALYSIS
    # -------------------------
    content.append(Paragraph("Overall Sentiment Analysis", styles['Heading2']))

    for k,v in sentiments.items():
        content.append(Paragraph(f"{k}: {v}", styles['Normal']))

    # Interpretation
    if sentiments["Positive"] > sentiments["Negative"]:
        sentiment_text = "The overall sentiment is positive, indicating general satisfaction among students."
    elif sentiments["Negative"] > sentiments["Positive"]:
        sentiment_text = "Negative sentiment dominates, suggesting potential issues in course delivery."
    else:
        sentiment_text = "Sentiment distribution is balanced, indicating mixed student experiences."

    content.append(Spacer(1,8))
    content.append(Paragraph(sentiment_text, styles['Italic']))
    content.append(Spacer(1,12))

    # -------------------------
    # 5. PERFORMANCE + COGNITION
    # -------------------------
    content.append(Paragraph("Performance and Cognition Analysis", styles['Heading2']))

    content.append(Paragraph(f"High Performing Students: {high}", styles['Normal']))
    content.append(Paragraph(f"Average Performing Students: {medium}", styles['Normal']))
    content.append(Paragraph(f"Low Performing Students: {low}", styles['Normal']))
    content.append(Paragraph(f"Average Cognition Score: {avg_cognition}", styles['Normal']))

    # Insight
    if high > low:
        perf_text = "A larger proportion of students are high-performing, indicating strong academic outcomes."
    elif low > high:
        perf_text = "A significant number of students are low-performing, indicating potential learning difficulties."
    else:
        perf_text = "Performance distribution is balanced across categories."

    content.append(Spacer(1,8))
    content.append(Paragraph(perf_text, styles['Italic']))
    content.append(Spacer(1,12))

    # -------------------------
    # 6. ASPECT ANALYSIS
    # -------------------------
    content.append(Paragraph("Aspect-Based Analysis", styles['Heading2']))

    for asp, vals in aspects.items():
        clean_name = asp.replace("#3","").replace("#General","")

        content.append(Paragraph(f"{clean_name}", styles['Heading3']))

        for k,v in vals.items():
            content.append(Paragraph(f"{k}: {v}", styles['Normal']))

        # Insight per aspect
        dominant_asp = max(vals, key=vals.get)

        content.append(Paragraph(
            f"Major feedback for this aspect is {dominant_asp}.",
            styles['Italic']
        ))

        content.append(Spacer(1,10))

    # -------------------------
    # 7. KEY OBSERVATIONS (🔥 IMPORTANT)
    # -------------------------
    content.append(Paragraph("Key Observations", styles['Heading2']))

    observations = []

    if sentiments["Negative"] > sentiments["Positive"]:
        observations.append("Negative feedback outweighs positive feedback, indicating areas needing improvement.")

    if high > 0 and sentiments["Negative"] > 0:
        observations.append("Some high-performing students have expressed negative feedback, suggesting genuine concerns.")

    if avg_cognition < 0.5:
        observations.append("Low average cognition indicates potential academic challenges among students.")

    if not observations:
        observations.append("The course is performing well with no major concerns identified.")

    for obs in observations:
        content.append(Paragraph(f"- {obs}", styles['Normal']))

    # -------------------------
    # BUILD PDF
    # -------------------------
    doc.build(content)
    buffer.seek(0)

    return send_file(buffer,
                     as_attachment=True,
                     download_name="detailed_report.pdf",
                     mimetype='application/pdf')

@app.route("/demo")
def demo_page():
    return render_template("demo.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json()

    student_data = {
        "assignment1": float(data.get("assignment1", 0)),
        "assignment2": float(data.get("assignment2", 0)),
        "quiz": float(data.get("quiz", 0)),
        "presentation": float(data.get("extra", 0)),
        "attendance": float(data.get("attendance", 0)),
        "cgpa": float(data.get("cgpa", 0))
    }

    result = full_system(data.get("review"), student_data)

    return jsonify({
        "overall_sentiment": result["observed_overall"],
        "cognition_score": int(result.get("cognition_score", 0) * 100),
        "aspect_analysis": result.get("aspect_analysis_with_cognition", {})
    })

def load_reviews():
    if Review.query.first():
        return  # ✅ prevents reloading every time

    reviews = [
        "The lectures were quite engaging and the instructor explained concepts clearly, but the assignments felt a bit too heavy.",
        "I liked the overall course structure, though sometimes the workload was overwhelming.",
        "The teacher made the lectures interesting, but the exam was tougher than expected.",
        "Course content was useful and practical, especially the examples provided in class.",
        "The assignments were manageable, but the lectures could have been more interactive.",
        "The instructor was very helpful, but the quiz questions were confusing at times.",
        "Good course overall, but the workload was not well balanced.",
        "The slides were detailed and helpful for revision, but lectures felt rushed.",
        "Exams were fair, but assignments required a lot of time.",
        "The teacher explained difficult topics well, but the course structure could improve.",

        "The course was decent overall, but the material provided was not sufficient.",
        "Lectures were clear, but the assignments were repetitive.",
        "I found the instructor approachable and supportive throughout the course.",
        "The workload was high, especially near the end of the course.",
        "Exams tested understanding well, but some questions were ambiguous.",
        "Course organization was good, but lectures lacked depth sometimes.",
        "The material and slides were very helpful for exams.",
        "Assignments were useful but time-consuming.",
        "The teacher was knowledgeable, but the pace of lectures was fast.",
        "Overall, a well-structured course with relevant content.",

        "The lectures were interesting, but the exams were quite difficult.",
        "Good instructor, but assignments were not clearly explained.",
        "Course content was useful, especially the examples discussed.",
        "The workload was manageable, but quizzes were tricky.",
        "The teacher made the class engaging and interactive.",
        "Slides were helpful, but more real-world examples would improve learning.",
        "Exams were fair, but assignments required more guidance.",
        "The course structure was clear and easy to follow.",
        "Instructor explained topics clearly, but lectures were sometimes monotonous.",
        "Overall experience was positive, though workload could be reduced.",

        "The assignments helped in understanding concepts better.",
        "The teacher was supportive, but lectures were sometimes too fast.",
        "Course material was comprehensive and easy to understand.",
        "Exams were challenging but fair.",
        "The course organization could be improved slightly.",
        "Lectures were informative, but slides lacked clarity.",
        "Overall, a good course with minor issues.",

        "The lectures were clear, but exams were too lengthy.",
        "The teacher was engaging and encouraged participation.",
        "Course content was relevant, but more examples were needed.",
        "Assignments were well designed and useful.",
        "The course structure was confusing at times.",
        "Exams were fair, but time pressure was high.",
        "The instructor explained concepts in a simple way.",
        "Slides were useful, but lectures lacked interaction.",
        "Workload was high, especially with multiple assignments.",
        "Overall, the course was good but could be improved.",

        "The teacher was very helpful and approachable.",
        "Lectures were interesting, but some topics were rushed.",
        "Course material was detailed and informative.",
        "Exams were difficult but covered important topics.",
        "The workload was manageable most of the time.",
        "The instructor explained concepts clearly.",
        "Slides and materials were very helpful for revision.",
        "Overall, a satisfying learning experience."
    ]

    students = Student.query.all()

    for i, student in enumerate(students):
        if i < len(reviews):
            db.session.add(Review(
                student_id=student.student_id,
                review_text=reviews[i],
                submitted=True
            ))

    db.session.commit()
# -------------------------
# RUN
# -------------------------

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        load_students()
        load_reviews()  # ✅ Load sample reviews only once
    app.run(debug=True)