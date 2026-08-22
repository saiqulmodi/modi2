import requests
import json

url = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
response = requests.get(url)
data = response.json()

with open("angel_scrips.json", "w") as f:
    json.dump(data, f)

print(f"Downloaded {len(data)} instruments to angel_scrips.json")
