FROM python:3.12-alpine
COPY dist/app.pyz /app/app.pyz
ENV BIND_HOST=0.0.0.0
EXPOSE 8000
CMD ["python", "/app/app.pyz", "--port", "8000"]
