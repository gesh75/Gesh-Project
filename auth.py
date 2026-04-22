import os
import robin_stocks.robinhood as rh
from dotenv import load_dotenv
load_dotenv()

def login():
    username = os.getenv("ROBINHOOD_USER")
    password = os.getenv("ROBINHOOD_PASS")
    if not username or not password:
        raise EnvironmentError("ROBINHOOD_USER and ROBINHOOD_PASS must be set in your .env file.")
    rh.login(username=username, password=password, expiresIn=86400, store_session=True, pickle_name="robinhood_session")

def logout():
    rh.logout()
