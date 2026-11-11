# 주의: 이 PC에 Docker가 없어 이 Dockerfile은 빌드 검증을 하지 못했다.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY data ./data
COPY scripts ./scripts
ENV AWS_DEFAULT_REGION=us-east-1
# AWS 자격 증명과 LangSmith 키는 이미지에 넣지 않고 실행 시 -e 또는 --env-file로 넘긴다.
EXPOSE 8000
# 벡터 색인은 커밋하지 않으므로 시작할 때 다시 만든다.
CMD ["sh", "-c", "python scripts/build_index.py && uvicorn src.server:app --host 0.0.0.0 --port 8000"]
