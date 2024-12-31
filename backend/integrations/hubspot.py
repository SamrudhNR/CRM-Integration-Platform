import secrets
import json
import httpx
import base64
import urllib.parse
from urllib.parse import urlencode
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse
from redis_client import add_key_value_redis, get_value_redis, delete_key_redis
import requests
from integrations.integration_item import IntegrationItem



CLIENT_ID = "cc0db89f-9e56-4175-a6dd-e0ba143df9b3"
CLIENT_SECRET = "6dad9a5d-4190-469d-9894-93cf3b3036a4"
REDIRECT_URI = "http://localhost:8000/integrations/hubspot/oauth2callback"
AUTHORIZATION_URL = "https://app.hubspot.com/oauth/authorize"
TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
SCOPES= "oauth crm.objects.companies.read crm.objects.contacts.read crm.lists.read crm.objects.custom.read crm.objects.users.read"


async def authorize_hubspot(user_id, org_id):
    # Generate a random state to prevent CSRF attacks
    state = secrets.token_urlsafe(32)
    print(f"Generated state: {state}")
    
    # Save the state in Redis for later validation
    state_data = {"user_id": user_id, "org_id": org_id, "state": state}

    encoded_state=base64.urlsafe_b64encode(json.dumps(state_data).encode('utf-8')).decode('utf-8')
    
    await add_key_value_redis(f"hubspot_state:{org_id}:{user_id}", json.dumps(state_data), expire=600)

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": encoded_state  #encoded as it is exposed
    }
    auth_url = f"{AUTHORIZATION_URL}?{urlencode(params)}"
    print("Generated OAuth URL:", auth_url)

    return auth_url

async def oauth2callback_hubspot(request: Request):
    if request.query_params.get('error'):
        raise HTTPException(status_code=400, detail="Error during OAuth2 callback")
    
    code = request.query_params.get("code")
    encoded_state = request.query_params.get("state")
    state_data= json.loads(base64.urlsafe_b64decode(encoded_state).decode('utf-8'))
    original_state= state_data.get('state')
    user_id = state_data.get('user_id')
    org_id = state_data.get('org_id')

    
 
    saved_state = await get_value_redis(f"hubspot_state:{org_id}:{user_id}")
    if not saved_state:
        raise HTTPException(status_code=400, detail="State does not match")

# Decode the byte string and load it as JSON
    saved_state_dict = json.loads(saved_state.decode('utf-8'))

# Compare the states
    if original_state != saved_state_dict.get('state'):
        raise HTTPException(status_code=400, detail="State does not match")


    
    # Request the access token using the authorization code
    async with httpx.AsyncClient() as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uri": REDIRECT_URI,
                "code": code,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    
    if response.status_code != 200:
        print(f"Failed to get access token. Status code: {response.status_code}")
        raise HTTPException(status_code=400, detail="Failed to get access token")
    
    credentials = response.json()
    
    # Save the credentials in Redis for future use
    await add_key_value_redis(f"hubspot_credentials:{org_id}:{user_id}", json.dumps(credentials), expire=600)
    print(f"Saved credentials to Redis for org_id:{org_id}, user_id:{user_id}")


    # Return a response to close the OAuth window
    close_window_script = """
    <html>
        <script>
            window.close();
        </script>
    </html>
    """
    return HTMLResponse(content=close_window_script)

async def get_hubspot_credentials(user_id, org_id):
    credentials = await get_value_redis(f"hubspot_credentials:{org_id}:{user_id}")
    if not credentials:
        raise HTTPException(status_code=400, detail="No credentials found")
    
    print(f"Retrieved credentials for org_id:{org_id}, user_id:{user_id}")
    return json.loads(credentials)

# async def create_integration_item_metadata_object(response_json):
#     # TODO
#     pass
def create_integration_item_metadata_object(response_json) -> IntegrationItem:
    properties = response_json.get('properties', {})
    # Creating an IntegrationItem object
    integration_item_metadata = IntegrationItem(
        id=response_json.get('id'),
        name=properties.get('firstname', "Unknown") + " " + properties.get('lastname', "Unknown"),
        type="Contact",  # You can use a type that fits the context, such as "Contact"
        creation_time=response_json.get('createdAt'),
        last_modified_time=response_json.get('updatedAt'),
        parent_id=None,  # If you need to set a parent, you can customize this
        parent_path_or_name=None,  # Similarly, update this if needed
        visibility=True,  # Assuming visibility as True unless stated otherwise
    )
    return integration_item_metadata


# Fetching the contact object data from HubSpot
def fetch_items(access_token: str, url: str, aggregated_response: list):
    """Fetching the list of objects"""
    headers = {'Authorization': f'Bearer {access_token}'}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        result = response.json().get('results')
        for item in result:
            aggregated_response.append(item)
    else:
        return


async def get_items_hubspot(credentials):
    credentials = json.loads(credentials)
    url = 'https://api.hubapi.com/crm/v3/objects/contacts'
    list_of_responses = []
    list_of_integration_item_metadata = []  # List to store IntegrationItem objects
    fetch_items(credentials.get('access_token'), url, list_of_responses)
    
    for response in list_of_responses:
        print(f"Processing contact: {response}\n") 
        # Converting each response into an IntegrationItem object
        list_of_integration_item_metadata.append(
            create_integration_item_metadata_object(response)
        )
    
    print(f'list_of_integration_item_metadata: {list_of_integration_item_metadata}')
    return list_of_integration_item_metadata


