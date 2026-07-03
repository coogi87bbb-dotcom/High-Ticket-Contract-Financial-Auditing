# Container image — used for Google Cloud Run (pairs with a Firebase/Google
# account) or any Docker host. Render uses render.yaml instead.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080
CMD exec uvicorn web.app:app --host 0.0.0.0 --port ${PORT}
