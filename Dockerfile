FROM python:3.11

ARG OPENAI_API_KEY
ARG OPENAI_MODEL
ARG GOOGLE_PLACES_API_KEY

ENV OPENAI_API_KEY=$OPENAI_API_KEY
ENV OPENAI_MODEL=$OPENAI_MODEL 
ENV GOOGLE_PLACES_API_KEY=$GOOGLE_PLACES_API_KEY

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r requirements.txt
RUN cat requirements.txt

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"]