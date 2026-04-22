import re

with open("server.py", "r") as f:
    text = f.read()

# Replace all async def with def EXCEPT ollama_second_opinion_endpoint which has an await
text = re.sub(r'^async def ', r'def ', text, flags=re.MULTILINE)

# Restore the ones that actually needed it
text = text.replace('def ollama_second_opinion_endpoint', 'async def ollama_second_opinion_endpoint')
text = text.replace('def lifespan', 'async def lifespan')

with open("server.py", "w") as f:
    f.write(text)

print("Server unblocked.")
