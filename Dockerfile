FROM python:3.10-alpine

ENV IS_DOCKER=True

ADD requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

WORKDIR /code

# Use python to execute the script instead of trying to run it directly
CMD ["sh", "-c", "python run.py migrate; python run.py"]
