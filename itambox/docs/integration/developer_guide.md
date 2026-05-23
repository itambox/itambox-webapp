# Developer Integration Guide

Welcome to the ITAMbox Developer Integration Guide. This document provides instructions on how to interact with the ITAMbox API, including authentication, resource query, and data updates.

## Authentication
To authenticate via the REST or GraphQL API, use the token authentication mechanism.
Pass the token in the HTTP `Authorization` header:
`Authorization: Token <your_api_token>`

Or, if accessing the UI, you can use session authentication.

## GraphQL API
The GraphQL endpoint is located at `/graphql`. You can query and mutate resources.
For example, to list assets, execute a POST request to `/graphql` with payload:
```json
{
  "query": "query { assets { id name assetTag } }"
}
```
