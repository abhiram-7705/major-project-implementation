import torch
from transformers import RobertaTokenizer, RobertaForSequenceClassification

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

print("Loading models...")

tokenizer = RobertaTokenizer.from_pretrained("./models/aspect_model")

model = RobertaForSequenceClassification.from_pretrained(
    "./models/aspect_model"
).to(device)

model_doc = RobertaForSequenceClassification.from_pretrained(
    "./models/overall_model"
).to(device)

model.eval()
model_doc.eval()

print("Models loaded successfully!\n")

# -------------------------
# ASPECT KEYWORDS
# -------------------------

aspect_keywords = {
    "Lecturer#3": ["teaching", "lecture", "lectures", "instructor", "teacher"],
    "Assessment": ["exam", "exams", "test", "tests", "assignment", "assignments", "quiz"],
    "Course Structure": ["structure", "organization", "workload"],
    "Subject Material": ["material", "materials", "slides", "content", "examples"],
    "Course#General": ["course", "overall"]
}


# -------------------------
# HELPER FUNCTIONS
# -------------------------

def highlight_aspect(text, aspect):
    text = text.lower()
    for word in aspect_keywords.get(aspect, []):
        if word in text:
            return text.replace(word, f"[ASP] {word} [ASP]")
    return text + f" [SEP] {aspect}"


def detect_aspects(review):
    review = review.lower()
    detected = []
    for aspect, keywords in aspect_keywords.items():
        if any(word in review for word in keywords):
            detected.append(aspect)
    return detected


def adjust_overall_neutral(review, sentiment):
    if any(word in review.lower() for word in ["average", "okay", "fine", "decent"]):
        return "Neutral"
    return sentiment


def adjust_negation(review, sentiment):
    review = review.lower()
    if "not bad" in review:
        return "Neutral"
    if "not good" in review:
        return "Negative"
    return sentiment


def adjust_suggestion(review, sentiment):
    review = review.lower()
    if ("needs" in review or "could" in review) and sentiment == "Positive":
        return "Neutral"
    return sentiment


def adjust_for_contrast(review, aspect, sentiment):
    review = review.lower()
    if "but" in review:
        parts = review.split("but")
        after = parts[1] if len(parts) > 1 else ""

        if any(word in after for word in aspect_keywords.get(aspect, [])):
            if any(nw in after for nw in ["confusing", "hard", "difficult", "unclear"]):
                if sentiment == "Positive":
                    return "Neutral"
    return sentiment


# -------------------------
# MODEL PREDICTION
# -------------------------

def predict(review, aspect):
    text = highlight_aspect(review, aspect)
    inputs = tokenizer(text, return_tensors="pt", truncation=True, padding=True).to(device)

    outputs = model(**inputs)
    pred = torch.argmax(outputs.logits, dim=1).item()

    return {0: "Negative", 1: "Neutral", 2: "Positive"}[pred]


def analyze_review(review):
    aspects = detect_aspects(review)
    results = {}

    for aspect in aspects:
        sentiment = predict(review, aspect)
        sentiment = adjust_for_contrast(review, aspect, sentiment)
        sentiment = adjust_negation(review, sentiment)
        sentiment = adjust_suggestion(review, sentiment)

        results[aspect] = sentiment

    return results


def predict_overall(review):
    inputs = tokenizer(review, return_tensors="pt", truncation=True, padding=True).to(device)

    outputs = model_doc(**inputs)
    pred = torch.argmax(outputs.logits, dim=1).item()

    sentiment = {0: "Negative", 1: "Neutral", 2: "Positive"}[pred]

    sentiment = adjust_overall_neutral(review, sentiment)
    sentiment = adjust_negation(review, sentiment)

    return sentiment


# -------------------------
# COGNITION
# -------------------------

def compute_cognition(student_data):
    a1 = student_data.get("assignment1", 0)
    a2 = student_data.get("assignment2", 0)
    quiz = student_data.get("quiz", 0)
    extra = student_data.get("presentation", 0)
    attendance = student_data.get("attendance", 0)

    # normalize CGPA (out of 10 → 100)
    cgpa = student_data.get("cgpa", 0) * 10

    # 🔥 UPDATED WEIGHTS (balanced)
    cognition = (
        ((a1 + a2) / 2) * 0.30 +
        quiz * 0.15 +
        extra * 0.10 +
        attendance * 0.15 +
        cgpa * 0.30
    ) / 100

    return round(cognition, 2)


def get_confidence(sentiment, cognition_score):
    if sentiment == "Neutral":
        if cognition_score > 0.8 or cognition_score < 0.4:
            return "Very Low"
        return "Low"

    return "High"


def interpret_sentiment(sentiment, cognition_score):
    explanation = ""

    if cognition_score > 0.8:
        level = "High cognition"
    elif cognition_score < 0.4:
        level = "Low cognition"
    else:
        level = "Moderate cognition"

    # -------------------------
    # CONTEXT LOGIC
    # -------------------------

    if sentiment == "Negative":
        if cognition_score > 0.8:
            explanation = "This concern is expressed despite strong academic performance, indicating a potentially genuine issue."
        elif cognition_score < 0.4:
            explanation = "This concern appears alongside lower academic performance, which may indicate learning challenges influencing perception."
        else:
            explanation = "This concern is observed with moderate performance, suggesting a balanced but notable issue."

    elif sentiment == "Positive":
        if cognition_score > 0.8:
            explanation = "Positive feedback aligns with strong academic performance, reinforcing credibility."
        elif cognition_score < 0.4:
            explanation = "Positive feedback despite lower performance may indicate supportive teaching."
        else:
            explanation = "Positive feedback with moderate performance suggests satisfactory learning experience."

    else:  # Neutral
        if cognition_score > 0.8:
            explanation = "Neutral feedback from a high-performing student may indicate unmet expectations."
        elif cognition_score < 0.4:
            explanation = "Neutral feedback alongside lower performance may reflect learning difficulties."
        else:
            explanation = "Neutral feedback suggests an average learning experience."

    return {
        "level": level,
        "explanation": explanation
    }

def is_reliable(sentiment):
    return sentiment != "Neutral"


# -------------------------
# FULL SYSTEM
# -------------------------

def full_system(review, student=None):
    overall = predict_overall(review)
    aspects = analyze_review(review)

    result = {
        "review": review,
        "observed_overall": overall,
        "observed_aspects": aspects
    }

    if student:
        cognition_score = compute_cognition(student)

        result["cognition_score"] = round(cognition_score, 3)
        result["overall_confidence"] = get_confidence(overall, cognition_score)
        result["overall_reliable"] = is_reliable(overall)
        overall_interp = interpret_sentiment(overall, cognition_score)
        result["overall_interpretation"] = overall_interp["explanation"]
        result["overall_cognition_level"] = overall_interp["level"]

        interpreted_aspects = {}
        for asp, sent in aspects.items():
            interp = interpret_sentiment(sent, cognition_score)

            interpreted_aspects[asp] = {
                "sentiment": sent,
                "confidence": get_confidence(sent, cognition_score),
                "reliable": is_reliable(sent),
                "cognition_level": interp["level"],
                "explanation": interp["explanation"]
            }

        result["aspect_analysis_with_cognition"] = interpreted_aspects

    return result