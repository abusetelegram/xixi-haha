FROM node:lts-alpine3.16

# Create app directory
WORKDIR /usr/src/app

ENV BOT_TOKEN=
ENV WEBHOOK_PATH /secret-path
ENV WEBHOOK_PORT 5000

# Install app dependencies
# A wildcard is used to ensure both package.json AND package-lock.json are copied
# where available (npm@5+)
COPY package*.json ./

RUN npm install

COPY . .

EXPOSE 5000

CMD [ "npm", "run", "start:webhook" ]