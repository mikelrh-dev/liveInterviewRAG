# Mikel — Projects

> **Nota**: Estos son proyectos reales del bootcamp DAM. Sustituye con tus datos reales.

## InterviewTTS — Voice AI Portfolio

**Technologies**: Python, FastAPI, Whisper, Edge TTS, Sentence Transformers, RAG

A voice-based AI portfolio that lets recruiters have natural voice conversations with a digital twin of the candidate. Instead of just reading a CV, recruiters can ask questions and receive spoken responses as if talking to the real person.

### Key Features
- **Voice Input**: Browser-based microphone recording with MediaRecorder API
- **Speech-to-Text**: Faster Whisper for accurate transcription of recruiter questions
- **RAG Pipeline**: Retrieves relevant context from candidate documents for accurate responses
- **LLM Generation**: Uses Owl API to generate natural, context-aware responses as the candidate
- **Voice Output**: Edge TTS synthesizes responses in a professional voice
- **Clean UI**: Responsive frontend designed for professional presentation

### Technical Highlights
- Built complete STT → RAG → LLM → TTS pipeline
- Implemented in-memory cosine similarity retrieval with sentence-transformers
- Designed for deployment on Oracle Free Tier with Nginx reverse proxy
- Professional frontend suitable for embedding in portfolio sites

### Challenges Solved
- **Latency Optimization**: Pipeline designed for under 8 seconds total response time
- **Audio Format Handling**: Browser webm/ogg converted to WAV via pydub for Whisper compatibility
- **Context Accuracy**: RAG pipeline ensures responses are grounded in real CV/project data
- **Error Graceful Degradation**: Each pipeline stage handles failures with meaningful error messages

## Gestión de Biblioteca (CRUD) — Java + Spring Boot

**Technologies**: Java, Spring Boot, MySQL, Thymeleaf, Bootstrap

Full-stack web application for library management. Allows librarians to manage books, members, and loans through a clean CRUD interface.

### Key Features
- Book catalog with search, filter, and pagination
- Member registration and management
- Loan tracking with due dates and late return alerts
- REST API endpoints for external integrations

### Technical Highlights
- Layered architecture (Controller → Service → Repository)
- MySQL database with JPA/Hibernate ORM
- Input validation and error handling
- Responsive UI with Bootstrap

## Sistema de Enrolamiento (CRUD) — PHP + Laravel

**Technologies**: PHP, Laravel, PostgreSQL, Blade, Tailwind CSS

Student enrollment system for managing courses, students, and enrollments with role-based access control.

### Key Features
- Admin dashboard for course and student management
- Student self-enrollment with prerequisites validation
- PDF report generation for enrollment certificates
- Email notifications for enrollment confirmation

### Technical Highlights
- MVC architecture with Laravel Eloquent ORM
- PostgreSQL with migrations and seeders
- Role-based authorization (admin, staff, student)
- RESTful API design

## Inventario de Productos (CRUD) — Python + Flask

**Technologies**: Python, Flask, SQLite, Jinja2, Bootstrap

Simple inventory management system for tracking product stock, categories, and suppliers.

### Key Features
- Product CRUD with category and supplier management
- Stock level tracking with low-stock alerts
- Barcode/QR code generation for products
- Export inventory to CSV/Excel

### Technical Highlights
- Flask with Blueprints for modular organization
- SQLite database with SQLAlchemy ORM
- Responsive design with Bootstrap 5
- Input sanitization and CSRF protection
