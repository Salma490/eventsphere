from fastapi import FastAPI

app = FastAPI(title="EventSphere")

@app.get("/")
def home():
    return {"message": "EventSphere is running"}