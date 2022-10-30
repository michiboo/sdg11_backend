FROM python:3.9
WORKDIR /usr/src/app
COPY . /usr/src/app
RUN chmod +x ./rabbitsetup.sh
RUN ./rabbitsetup.sh
RUN python3 -m pip install --upgrade pip
RUN python3 -m pip install --upgrade flask flask-cors pyopenssl osmnx utm cityseer==3.6.0 celery PyAMQP
RUN python3 -m pip install pandana


EXPOSE 5000
# RUN cd ./cityseer-api && python3 -m pip install .
CMD chmod +x ./start.sh; ./start.sh

# docker run -it -v ./:./ -p 5000:5000 $(docker build -q .)
# docker run -it -v $(pwd)/graph:/usr/src/app/graph -p 5000:5000 $(docker build -q .)