FROM python:3.12.2-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
ENV UPSTREAM_CHAT_COMPLETIONS_URL="https://api.featherless.ai/v1/chat/completions"
CMD ["uvicorn", "adapter:app", "--host", "0.0.0.0", "--port", "80"]