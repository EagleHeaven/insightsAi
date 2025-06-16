FROM python:3.11

ENV OPENAI_API_KEY=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
ENV OPENAI_MODEL=gpt-4o
ENV GOOGLE_PLACES_API_KEY=AIzaSyCF7K2RmXt1ZQVUkysKL0vOfnLSHs9OoM4

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r requirements.txt
RUN cat requirements.txt

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"]