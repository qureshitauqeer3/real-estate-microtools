from fastapi import FastAPI, File, UploadFile, Form
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import shutil
import uvicorn

app = FastAPI()

STATIC_DIR = Path("static")
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.post("/v1/mutate")
async def mutate_post(media: UploadFile = File(...), caption: str = Form(...)):
    filename = f"mutated_{media.filename}"
    file_path = STATIC_DIR / filename

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(media.file, buffer)

    return {
        "mutated_caption": (
            "Empty listings sit on the market 4x longer.\n\n"
            "We virtually staged this vacant living room in under 30 seconds using AI (Japandi Warm Minimalist style) — keeping 100% of the original hardwood floors, walls, and architectural fixtures intact for MLS compliance.\n\n"
            "No expensive staging furniture rentals.\n"
            "Zero moving trucks.\n"
            "Fast 24hr turnaround for listing agents & property photographers.\n\n"
            "Want to see how your vacant listing looks fully furnished?\n"
            "Drop a comment 'STAGE' below or DM us your photo, and we'll stage one room for FREE as a sample.\n\n"
            ".\n.\n.\n"
            "#virtualstaging #realestateagent #realestatemarketing #propertystaging #homestaging #luxuryrealestate #interiordesign #listingagent #realtorlife #realestateinvesting #propertyphotography"
        ),
        "mutated_media_type": "image",
        "mutated_media_url": f"http://localhost:20129/static/{filename}"
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=20129)