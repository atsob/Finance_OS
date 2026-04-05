FROM python:3.13-slim

# Απαραίτητο για να είναι καθαρός ο φάκελος προορισμού
WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Τώρα το "." αναφέρεται στον άδειο φάκελο /app
RUN git clone https://github.com/atsob/Finance_OS.git .

# Προσοχή στα paths: Αν το repo έχει φάκελο app/, άφησέ το έτσι. 
# Αν τα αρχεία είναι χύμα, σβήσε το "app/" από παντού.
RUN pip install --upgrade pip setuptools certifi && \
    pip install -r app/requirements.txt

EXPOSE 8501

HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health

# (πρόσθεσε αυτά πριν το ENTRYPOINT αν θες defaults)
ENV DB_NAME=Finance
ENV DB_HOST=192.168.4.20
DB_PORT=5432
DB_USER=admin
DB_PASSWORD=31.12.1969

ENTRYPOINT ["streamlit", "run", "app/Finance_OS.py", "--server.port=8501", "--server.address=0.0.0.0"]

