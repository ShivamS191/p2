from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from playwright.async_api import async_playwright
from openai import OpenAI
import httpx
import asyncio
import json
import base64
from typing import Any, Optional
import os

app = FastAPI()

# Configuration
SECRET = "hello"  # Your secret
EMAIL = "your-email@example.com"  # Replace with your email
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Set this as environment variable

client = OpenAI(api_key=OPENAI_API_KEY)

class QuizRequest(BaseModel):
    email: str
    secret: str
    url: str

class QuizResponse(BaseModel):
    status: str
    message: str

@app.post("/quiz")
async def handle_quiz(request: QuizRequest):
    """Main endpoint to receive and solve quiz tasks"""
    
    # Verify secret
    if request.secret != SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")
    
    # Verify email
    if request.email != EMAIL:
        raise HTTPException(status_code=400, detail="Invalid email")
    
    # Start solving the quiz
    asyncio.create_task(solve_quiz_chain(request.url, request.email, request.secret))
    
    return QuizResponse(status="success", message="Quiz solving started")

async def solve_quiz_chain(url: str, email: str, secret: str):
    """Solve quiz and handle chaining to next quizzes"""
    current_url = url
    start_time = asyncio.get_event_loop().time()
    
    while current_url:
        # Check if 3 minutes have passed
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed > 180:  # 3 minutes
            print(f"Time limit exceeded: {elapsed} seconds")
            break
        
        try:
            # Fetch and solve the quiz
            quiz_content = await fetch_quiz_page(current_url)
            answer = await solve_quiz_with_llm(quiz_content, current_url)
            
            # Submit the answer
            result = await submit_answer(email, secret, current_url, answer)
            
            if result.get("correct"):
                print(f"✅ Correct answer for {current_url}")
                current_url = result.get("url")  # Get next quiz URL
                if not current_url:
                    print("🎉 Quiz completed!")
                    break
            else:
                print(f"❌ Wrong answer: {result.get('reason')}")
                # Retry or move to next if provided
                next_url = result.get("url")
                if next_url and next_url != current_url:
                    current_url = next_url
                else:
                    # Retry with same URL
                    await asyncio.sleep(1)
                    
        except Exception as e:
            print(f"Error solving quiz: {e}")
            break

async def fetch_quiz_page(url: str) -> str:
    """Fetch quiz page content using headless browser"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            # Wait for content to render
            await page.wait_for_timeout(2000)
            
            # Get the full page content
            content = await page.content()
            
            # Try to get specific result div if exists
            try:
                result_div = await page.query_selector("#result")
                if result_div:
                    result_content = await result_div.inner_html()
                    content = result_content
            except:
                pass
            
            await browser.close()
            return content
            
        except Exception as e:
            await browser.close()
            raise Exception(f"Failed to fetch page: {e}")

async def solve_quiz_with_llm(quiz_content: str, quiz_url: str) -> Any:
    """Use OpenAI GPT to solve the quiz"""
    
    prompt = f"""You are an expert data analyst. You have received a quiz task.

Quiz URL: {quiz_url}

Quiz Content (HTML):
{quiz_content}

Your task:
1. Extract the question from the HTML content (it may be base64 encoded)
2. If there's a file to download, note the URL
3. Understand what analysis is required
4. Provide the answer in the exact format requested

The answer could be:
- A number
- A string
- A boolean
- A base64 URI
- A JSON object

Important: 
- If you need to download and analyze a file (PDF, CSV, Excel), describe what steps to take
- For calculations, show your work
- Return ONLY the final answer value, nothing else

Analyze the content and provide the answer:"""

    response = client.chat.completions.create(
        model="gpt-4o",  # Using GPT-4o (best for reasoning)
        messages=[
            {"role": "system", "content": "You are a data analysis expert. Provide precise, accurate answers."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.1,
        max_tokens=4000
    )
    
    answer_text = response.choices[0].message.content
    
    # Try to parse as JSON, number, or return as string
    try:
        return json.loads(answer_text)
    except:
        try:
            return int(answer_text.strip())
        except:
            try:
                return float(answer_text.strip())
            except:
                return answer_text.strip()

async def submit_answer(email: str, secret: str, url: str, answer: Any) -> dict:
    """Submit answer to the quiz endpoint"""
    
    # Extract submit URL from the quiz page
    quiz_content = await fetch_quiz_page(url)
    
    # Try to find submit URL in the content
    submit_url = extract_submit_url(quiz_content, url)
    
    payload = {
        "email": email,
        "secret": secret,
        "url": url,
        "answer": answer
    }
    
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        try:
            response = await http_client.post(submit_url, json=payload)
            return response.json()
        except Exception as e:
            print(f"Error submitting answer: {e}")
            return {"correct": False, "reason": str(e)}

def extract_submit_url(html_content: str, default_url: str) -> str:
    """Extract submit URL from quiz content"""
    # Look for submit URL patterns
    import re
    
    # Try to find URLs in the content
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    urls = re.findall(url_pattern, html_content)
    
    # Look for 'submit' in URLs
    for url in urls:
        if 'submit' in url.lower():
            return url
    
    # Default: assume submit endpoint
    base = default_url.rsplit('/', 1)[0]
    return f"{base}/submit"

@app.get("/")
async def root():
    return {"message": "LLM Quiz Analyzer API is running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)