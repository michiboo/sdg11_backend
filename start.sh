service rabbitmq-server start
celery -A urbanAnalysis.celery worker &
python3 urbanAnalysis.py run