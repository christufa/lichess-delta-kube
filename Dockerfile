FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV HF_HUB_DISABLE_PROGRESS_BARS=1

COPY src/ ./src/

ENTRYPOINT ["python", "src/pipeline.py"]
