# API Specification

## Endpoint: POST /analyze

### Request

{
"query": "string"
}

### Response

{
"answer": "string"
}

---

## Flow

1. Receive user query
2. Send query to LLM with tools
3. If tool call:

   * execute tool
   * return result to LLM
4. Repeat until final answer
5. Return response
