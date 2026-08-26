# Build the React/Vite dashboard.
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Run the FastAPI application. HTTPS is terminated by the hosting platform or
# reverse proxy, which must forward X-Forwarded-Proto.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=production
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
EXPOSE 8000
CMD ["uvicorn", "webapp:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
