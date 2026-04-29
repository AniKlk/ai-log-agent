# Data Contracts

## Log Event Schema

{
"timestamp": "ISO string",
"message": "string",
"type": "info | error | disconnect"
}

---

## Error Schema

{
"timestamp": "ISO string",
"error": "string"
}

---

## Timeline Schema

{
"timestamp": "ISO string",
"event": "string"
}

---

## Rules

* All timestamps must be normalized
* Events must be ordered
* Errors must be clearly separated
