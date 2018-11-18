# This file is a template, and might need editing before it works on your project.
FROM node:10.6-alpine

# Uncomment if use of `process.dlopen` is necessary
# apk add --no-cache libc6-compat

ENV PORT 3000
EXPOSE 3000 # replace this with your application's default port, if necessary

WORKDIR /usr/src/app
COPY . .

CMD [ "node", "index.js" ]
