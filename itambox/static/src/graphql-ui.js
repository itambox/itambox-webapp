import { createGraphiQLFetcher } from '@graphiql/toolkit';
import { GraphiQL } from 'graphiql';
import { createClient } from 'graphql-ws';
import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';

const settings = window.GRAPHENE_SETTINGS || {};
const parameters = new URLSearchParams(window.location.hash.slice(1));
const fetchURL = `${window.location.pathname}${window.location.search}`;
const subscriptionPath = settings.subscriptionPath || window.location.pathname;
const subscriptionURL = `${window.location.origin.replace(/^http/, 'ws')}${subscriptionPath}`;
const csrfToken =
  document.cookie.match(/(?:^|; )csrftoken=([^;]+)/)?.[1] ||
  document.querySelector('[name="csrfmiddlewaretoken"]')?.value;
const headers = csrfToken ? { 'X-CSRFToken': csrfToken } : {};

const fetcher = createGraphiQLFetcher({
  url: fetchURL,
  headers,
  wsClient: createClient({
    url: subscriptionURL,
    lazy: true,
    shouldRetry: () => true,
  }),
});

function readParameter(name) {
  return parameters.get(name) || undefined;
}

function updateURL(nextParameters) {
  const hash = new URLSearchParams();
  for (const [key, value] of Object.entries(nextParameters)) {
    if (value) hash.set(key, value);
  }
  const url = new URL(window.location.href);
  url.hash = hash.toString();
  window.history.replaceState(null, '', url);
}

function GraphiQLApp() {
  const [query, setQuery] = useState(readParameter('query'));
  const [variables, setVariables] = useState(readParameter('variables'));
  const [operationName, setOperationName] = useState(readParameter('operationName'));

  const syncURL = (nextQuery, nextVariables, nextOperationName) => {
    updateURL({
      query: nextQuery,
      variables: nextVariables,
      operationName: nextOperationName,
    });
  };

  return React.createElement(GraphiQL, {
    fetcher,
    defaultEditorToolsVisibility: true,
    query,
    variables,
    operationName,
    onEditQuery: (nextQuery) => {
      setQuery(nextQuery);
      syncURL(nextQuery, variables, operationName);
    },
    onEditVariables: (nextVariables) => {
      setVariables(nextVariables);
      syncURL(query, nextVariables, operationName);
    },
    onEditOperationName: (nextOperationName) => {
      setOperationName(nextOperationName);
      syncURL(query, variables, nextOperationName);
    },
    isHeadersEditorEnabled: settings.graphiqlHeaderEditorEnabled,
    shouldPersistHeaders: settings.graphiqlShouldPersistHeaders,
    inputValueDeprecation: settings.graphiqlInputValueDeprecation,
  });
}

createRoot(document.getElementById('editor')).render(React.createElement(GraphiQLApp));
