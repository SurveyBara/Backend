from openai import OpenAI
import openai
from base64 import b64encode
import json
from dotenv import load_dotenv
from tarsier import Tarsier, GoogleVisionOCRService
import time
import re
import os
import requests
import aiofiles

load_dotenv()

port = os.getenv("PORT")

google_cloud_credentials = json.loads(os.getenv("GOOGLE_CLOUD_CREDENTIALS"))

ocr_service = GoogleVisionOCRService(google_cloud_credentials)
tarsier = Tarsier(ocr_service)

model = OpenAI()
model.timeout = 30


class WebAgent:
    def __init__(self, page) -> None:
        self.base64_image = None
        self.tag_to_xpath = {}
        self.page_text = ""
        self.instructions = """
            You are a website browsing agent. You will be given instructions on what to do by browsing. You are connected to a web browser and you will be given the screenshot and the text representation of the website you are on. 
            You can interact with the website by clicking on links, filling in text boxes, and going to a specific URL.
            
            [#ID]: text-insertable fields (e.g. textarea, input with textual type)
            [@ID]: hyperlinks (<a> tags)
            [$ID]: other interactable elements (e.g. button, select)
            [ID]: plain text (if you pass tag_text_elements=True)

            You can go to a specific URL by answering with the following JSON format:
            {"url": "url goes here"}

            You can click links on the website by referencing the ID before the component in the text representation, by answering in the following JSON format:
            {"click": "ID"}

            You can fill in text boxes by referencing the ID before the component in the text representation, by answering in the following JSON format:
            {"input": {"select": "ID", "text": "Text to type"}}

            Don't include the #, @, or $ in the ID when you are answering with the JSON format.

            The IDs are always integer values.

            You can press any key on the keyboard by answering with the following JSON format:
            {"keyboard": "key"}
            make sure your input for "key" works for the page.keyboard.press method from python playwright.

            You can go back, go forward, or reload the page by answering with the following JSON format:
            {"navigation": "back"}
            {"navigation": "forward"}
            {"navigation": "reload"}

            You can record the reachout by answering with the following JSON format:
            {"record reachout": {"email": "Email", "keyword": "Keyword", "question": "Question", "name": "Name of the reachout"}}

            You can delete the reachout by answering with the following JSON format:
            {"delete reachout": {"email": "Email", "keyword": "Keyword", "question": "Question", "name": "Name of the reachout"}}

            You can record the response by answering with the following JSON format:
            {"record response": {"email": "Email", "keyword": "Keyword", "question": "Question", "name": "Name of the reachout", "response": "Response"}}

            When responding with the JSON format, only include ONE JSON object and nothing else, no need for explanation.

            Once you are on a URL and you have found the answer to the user's question, you can answer with a regular message.

            Use google search by set a sub-page like 'https://google.com/search?q=search
        """
        self.messages = [
            {"role": "system", "content": self.instructions},
        ]
        self.page = page

    def image_b64(self, image):
        with open(image, "rb") as f:
            return b64encode(f.read()).decode("utf-8")

    async def write_text_to_file(self, file_name, text):
        async with aiofiles.open(file_name, "w") as file:
            await file.write(text)

    async def process_page(self):
        try:
            await self.page.wait_for_timeout(2000)
            print("Getting text...")
            page_text, tag_to_xpath = await tarsier.page_to_text(
                self.page, tag_text_elements=True
            )
            await self.write_text_to_file("page_text.txt", page_text)
            print("Taking screenshot...")
            await self.page.screenshot(path="screenshot.jpg", full_page=True)
        except Exception as e:
            print(e)
            return

        self.base64_image = self.image_b64("screenshot.jpg")
        self.tag_to_xpath = tag_to_xpath
        self.page_text = page_text

    def extract_json(self, message):
        json_regex = r"\{[\s\S]*\}"
        matches = re.findall(json_regex, message)

        if matches:
            try:
                # Assuming the first match is the JSON we want
                json_data = json.loads(matches[0])
                return json_data
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON: {e}")
                return {}
        else:
            print("No JSON found in the message")
            return {}

    async def write_code(self, input, new_code):
        with open("code.py", "r") as file:
            existing_code = file.read()
            response = model.chat.completions.create(
                model="gpt-4-0125-preview",
                messages=[{
                    "role": "system",
                    "content": f"""
                        You are a python code writing agent.
                        Here is the code you have written so far:
                        {existing_code}
                        You will be given a comment and a code snippet to add to the code.
                        You will need to add the code snippet to the code and add the comment to the code.
                        If the comment mentions if statements, loops, or any other control flow, you will need to add the code snippet to the control flow.
                        Your response should only be the updated code, it will go straight into the python file.
                    """,
                },{
                    "role": "user",
                    "content": f"""
                        Here is the comment for this line of code that I want to add: {input}
                        Here is the code I want to add: {new_code}
                    """,
                }],
                max_tokens=4096,
            )
            message = response.choices[0].message
            message_text = message.content
            print("Code Assistant:", message_text)
            # replace the existing code with the message_text
            with open("code.py", "w") as file:
                file.write(message_text)


    async def chat(self, input):
        self.messages.append(
            {
                "role": "user",
                "content": input,
            }
        )

        print("User:", input)

        while True:
            if self.base64_image:
                self.messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{self.base64_image}"
                                },
                            },
                            {
                                "type": "text",
                                "text": f"""Here's the screenshot of the website you are on right now.
                                \n{self.instructions}\n
                                Here's the text representation of the website:
                                \n{self.page_text}
                                """,
                            },
                        ],
                    }
                )

                self.base64_image = None

            for attempt in range(3):
                try:
                    response = model.chat.completions.create(
                        model="gpt-4-vision-preview",
                        messages=self.messages,
                        max_tokens=1024,
                    )
                    break
                except openai.RateLimitError as e:
                    print(
                        f"Rate limit exceeded, attempt {attempt + 1} of {3}. Retrying in {120} seconds..."
                    )
                    time.sleep(120)

            if not response:
                raise Exception("API call failed after retrying")

            message = response.choices[0].message
            message_text = message.content

            self.messages.append(
                {
                    "role": "assistant",
                    "content": message_text,
                }
            )

            self.messages = [self.messages[0]] + self.messages[-4:]

            print("Browser Assistant:", message_text)

            data = self.extract_json(message_text)
            try:
                if "click" in data:
                    id = int(data["click"])
                    elements = await self.page.query_selector_all(self.tag_to_xpath[id])
                    if elements:
                        await elements[0].click()
                        self.write_code(
                            f"""
                            elements = await page.query_selector_all('{self.tag_to_xpath[id]}')
                            if elements:
                                await elements[0].click()
                        """
                        )
                    await self.process_page()
                    continue
                elif "url" in data:
                    url = data["url"]
                    await self.page.goto(url)
                    self.write_code(f"await page.goto('{url}')")
                    await self.process_page()
                    continue
                elif "input" in data:
                    id = int(data["input"]["select"])
                    text_to_type = data["input"]["text"]
                    elements = await self.page.query_selector_all(self.tag_to_xpath[id])
                    if elements:
                        await elements[0].type(text_to_type)
                        self.write_code(
                            f"""
                            elements = await page.query_selector_all('{self.tag_to_xpath[id]}')
                            if elements:
                                await elements[0].type('{text_to_type}')
                        """
                        )
                    await self.process_page()
                    continue
                elif "keyboard" in data:
                    key = data["keyboard"]
                    await self.page.keyboard.press(key)
                    self.write_code(f"await page.keyboard.press('{key}')")
                    await self.process_page()
                    continue
                elif "navigation" in data:
                    navigation = data["navigation"]
                    if navigation == "back":
                        await self.page.go_back()
                        self.write_code("await page.go_back()")
                    elif navigation == "forward":
                        await self.page.go_forward()
                        self.write_code("await page.go_forward()")
                    elif navigation == "reload":
                        await self.page.reload()
                        self.write_code("await page.reload()")
                    await self.process_page()
                    continue
                elif "record response" in data:
                    email = data["record response"]["email"]
                    keyword = data["record response"]["keyword"]
                    question = data["record response"]["question"]
                    name = data["record response"]["name"]
                    response = data["record response"]["response"]
                    print(f"Recording response for {name}: {response}")
                    url = "http://localhost/record-response"

                    data = {
                        "email": email,
                        "keyword": keyword,
                        "question": question,
                        "name": name,
                        "response": response,
                    }

                    response = requests.post(url, json=data)

                    print(response.status_code)
                    print(response.text)
                elif "record reachout" in data:
                    email = data["record reachout"]["email"]
                    keyword = data["record reachout"]["keyword"]
                    question = data["record reachout"]["question"]
                    name = data["record reachout"]["name"]
                    print(
                        f"Recording reachout for name: {name}, email: {email}, keyword: {keyword}, question: {question}"
                    )
                    url = f"http://localhost:{port}/record-reachout"
                    data = {
                        "email": email,
                        "keyword": keyword,
                        "question": question,
                        "name": name,
                    }
                    response = requests.post(url, json=data)
                    print(response.status_code)
                    print(response.text)
                elif "delete reachout" in data:
                    email = data["delete reachout"]["email"]
                    keyword = data["delete reachout"]["keyword"]
                    question = data["delete reachout"]["question"]
                    name = data["delete reachout"]["name"]
                    print(
                        f"Deleting reachout for name: {name}, email: {email}, keyword: {keyword}, question: {question}"
                    )
                    url = f"http://localhost:{port}/delete-reachout"
                    data = {
                        "email": email,
                        "keyword": keyword,
                        "question": question,
                        "name": name,
                    }
                    response = requests.post(url, json=data)
                    print(response.status_code)
                    print(response.text)
            except TimeoutError as e:
                self.messages.append(
                    {
                        "role": "assistant",
                        "content": f"TimeoutError occurred: {e}",
                    }
                )
                continue
            return message_text