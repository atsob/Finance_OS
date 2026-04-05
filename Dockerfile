# app/Dockerfile
# Python usage, definition of task, copy of files, installation, port definition and start up
FROM python:3.14-slim
WORKDIR /app
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    software-properties-common \
    git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/atsob/Finance_OS.git .
RUN pip3 install -r requirements.txt
EXPOSE 8501
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health
ENTRYPOINT ["streamlit", "run", "Finance_OS.py", "--server.port=8501", "--server.address=0.0.0.0"]

