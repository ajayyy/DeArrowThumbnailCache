FROM python:3.11-alpine
RUN apk add --no-cache ffmpeg
COPY . /app
WORKDIR /app
RUN pip install -r requirements.txt
EXPOSE 3000
CMD ["python", "app.py"]