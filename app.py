from flask import Flask, render_template, request, jsonify, Response
import sqlite3
import json
import uuid
import datetime
import random
import docx
import io
import re
import csv

app = Flask(__name__)

# --- НАСТРОЙКА БАЗЫ ДАННЫХ SQLITE ---
def get_db_connection():
    conn = sqlite3.connect('smartgrade.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row 
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS tests (
            test_id TEXT PRIMARY KEY,
            name TEXT,
            time_limit INTEGER,
            questions TEXT,
            created_at TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id TEXT,
            student_name TEXT,
            student_group TEXT,
            status TEXT,
            score INTEGER,
            total INTEGER,
            timestamp TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- ЛОГИКА ПАРСИНГА WORD ---
def parse_docx(file_stream):
    doc = docx.Document(file_stream)
    questions = []
    current_q = None
    
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text: continue
            
        if re.match(r'^\d+[\.\)]?\s+', text):
            if current_q and current_q["answer"]: 
                questions.append(current_q)
            clean_q_text = re.sub(r'^\d+[\.\)]?\s+', '', text)
            current_q = {"q": clean_q_text, "options": [], "answer": ""}
            
        elif current_q is not None:
            match = re.match(r'^([А-ЯA-Zа-яa-z])[\.\)]?\s+(.*)', text)
            if match:
                letter = match.group(1).upper()
                clean_opt_text = match.group(2).strip()
                current_q["options"].append(clean_opt_text)
                if letter in ['A', 'А']:
                    current_q["answer"] = clean_opt_text
            else:
                current_q["options"].append(text)
                if len(current_q["options"]) == 1:
                    current_q["answer"] = text

    if current_q and current_q["answer"]:
        questions.append(current_q)
    return questions

# --- РОУТЫ ПРЕПОДАВАТЕЛЯ ---
@app.route('/teacher', methods=['GET', 'POST'])
def teacher_dashboard():
    if request.method == 'POST':
        test_name = request.form.get('test_name')
        time_limit = int(request.form.get('time_limit', 15))
        test_id = str(uuid.uuid4())[:8] 
        file = request.files.get('test_file')
        
        if file and file.filename.endswith('.docx'):
            file_stream = io.BytesIO(file.read())
            questions = parse_docx(file_stream)
            if not questions: return "Ошибка: Не удалось найти вопросы в файле. Проверьте оформление."
            
            conn = get_db_connection()
            conn.execute('INSERT INTO tests (test_id, name, time_limit, questions, created_at) VALUES (?, ?, ?, ?, ?)',
                         (test_id, test_name, time_limit, json.dumps(questions, ensure_ascii=False), str(datetime.datetime.now())))
            conn.commit()
            conn.close()
            
            return f"""
            <!DOCTYPE html>
            <html lang="ru">
            <head>
                <meta charset="UTF-8">
                <title>Успех!</title>
                <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
            </head>
            <body style="background-color: #f0f4f8; font-family: sans-serif;">
                <div class="container py-5">
                    <div class="card p-5 text-center shadow-lg border-0" style="border-radius: 20px;">
                        <h2 class="text-success mb-4">✅ Тест '{test_name}' успешно создан!</h2>
                        <div class="mb-4">
                            <p class="text-muted mb-1">Отправьте эту ссылку студентам:</p>
                            <a href="/test/{test_id}" target="_blank" class="fs-4 fw-bold text-primary">http://127.0.0.1:5000/test/{test_id}</a>
                        </div>
                        <div class="mb-4">
                            <p class="text-muted mb-1">Ваша ссылка для результатов (сохраните её!):</p>
                            <a href="/results/{test_id}" target="_blank" class="fs-5 text-danger">http://127.0.0.1:5000/results/{test_id}</a>
                        </div>
                        <a href="/teacher" class="btn btn-outline-secondary mt-3" style="border-radius: 12px;">Создать еще один тест</a>
                    </div>
                </div>
            </body>
            </html>
            """
    return render_template('teacher.html')

@app.route('/results/<test_id>')
def view_results(test_id):
    conn = get_db_connection()
    test = conn.execute('SELECT * FROM tests WHERE test_id = ?', (test_id,)).fetchone()
    if not test: 
        conn.close()
        return "Тест не найден!", 404
        
    db_results = conn.execute('SELECT * FROM results WHERE test_id = ? ORDER BY id DESC', (test_id,)).fetchall()
    conn.close()
    
    results = []
    for r in db_results:
        results.append({
            "student_name": r["student_name"],
            "group": r["student_group"],
            "status": r["status"],
            "score": r["score"],
            "total": r["total"],
            "timestamp": r["timestamp"]
        })
        
    return render_template('results.html', test=test, results=results)

@app.route('/export/<test_id>')
def export_results(test_id):
    conn = get_db_connection()
    test = conn.execute('SELECT * FROM tests WHERE test_id = ?', (test_id,)).fetchone()
    if not test:
        conn.close()
        return "Тест не найден!", 404
        
    db_results = conn.execute('SELECT * FROM results WHERE test_id = ? ORDER BY id DESC', (test_id,)).fetchall()
    conn.close()
    
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Дата и время', 'ФИО студента', 'Группа', 'Статус', 'Набрано баллов', 'Всего вопросов'])
    
    for r in db_results:
        writer.writerow([
            r["timestamp"], r["student_name"],
            r["student_group"], r["status"],
            r["score"], r["total"]
        ])
        
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers["Content-Disposition"] = f"attachment; filename=Results_{test['name']}.csv"
    return response

# --- РОУТЫ СТУДЕНТА ---
@app.route('/test/<test_id>', methods=['GET', 'POST'])
def student_login(test_id):
    conn = get_db_connection()
    test_row = conn.execute('SELECT * FROM tests WHERE test_id = ?', (test_id,)).fetchone()
    conn.close()
    
    if not test_row: return "Тест не найден!", 404
    
    test = dict(test_row)
    test['questions'] = json.loads(test['questions']) 
    
    if request.method == 'POST':
        student_name = request.form.get('student_name')
        group = request.form.get('group')
        
        shuffled_questions = test['questions'].copy()
        random.shuffle(shuffled_questions)
        for q in shuffled_questions: random.shuffle(q['options'])
        
        return render_template('test_active.html', test={"name": test["name"], "questions": shuffled_questions, "test_id": test_id, "time_limit": test["time_limit"]}, student_name=student_name, group=group)
        
    return render_template('student_login.html', test=test)

@app.route('/submit_test', methods=['POST'])
def submit_test():
    data = request.json
    test_id = data.get('test_id')
    
    conn = get_db_connection()
    test_row = conn.execute('SELECT * FROM tests WHERE test_id = ?', (test_id,)).fetchone()
    
    if not test_row: 
        conn.close()
        return jsonify({"message": "Ошибка базы данных"}), 400
        
    questions = json.loads(test_row['questions'])
    score = 0
    correct_answers = {q['q']: q['answer'] for q in questions}
    
    for q_text, ans in data.get('answers', {}).items():
        if correct_answers.get(q_text) == ans: score += 1
        
    conn.execute('''
        INSERT INTO results (test_id, student_name, student_group, status, score, total, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (test_id, data['student_name'], data['group'], data['status'], score, len(questions), datetime.datetime.now().strftime("%d.%m.%Y %H:%M")))
    conn.commit()
    conn.close()
    
    return jsonify({"score": score, "total": len(questions)})

if __name__ == '__main__':
    app.run(debug=True)