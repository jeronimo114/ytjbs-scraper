import time
import csv
import os
import traceback
from datetime import datetime, timedelta

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    ElementClickInterceptedException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

from bs4 import BeautifulSoup
import logging

# -----------------------------------------------------------------------------
# Configuration Section
# -----------------------------------------------------------------------------
URL = "https://ytjobs.co"           # The job listings page
CHECK_INTERVAL = 3600               # Check for new jobs every 1 hour (in seconds)
RETRY_LIMIT = 3                     # Maximum retries for processing each job
CSV_FILE_NAME = "job_listings.csv"  # Where scraped jobs will be stored
TODAY_JOBS_FILE = "today_jobs.csv"  # Where today's jobs will be stored
LOG_FILE_NAME = "job_scraper.log"   # Log file for debugging and tracking

# -----------------------------------------------------------------------------
# Setup Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Formatter to include timestamp, log level, and message
formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(message)s')

# File Handler - logs to a file
file_handler = logging.FileHandler(LOG_FILE_NAME)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Stream Handler - logs to console
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# -----------------------------------------------------------------------------
# 1. Set up the Selenium WebDriver
# -----------------------------------------------------------------------------
def setup_driver():
    """
    Initializes and returns a headless Chrome WebDriver using webdriver_manager.
    """
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # Run Chrome in headless mode
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")  # Optional: Set window size

    # Initialize the Service object with the path to ChromeDriver
    service = Service(ChromeDriverManager().install())

    # Initialize the WebDriver with the Service and options
    driver = webdriver.Chrome(service=service, options=chrome_options)
    logging.info("WebDriver initialized and navigating to the job listings page.")
    driver.get(URL)
    return driver

# -----------------------------------------------------------------------------
# 2. Load all job listings by clicking "Load More"
# -----------------------------------------------------------------------------
def load_all_jobs(driver):
    """
    Continues to click the 'Load More' button until no more jobs can be loaded.
    Uses explicit waits to handle dynamic loading.
    """
    while True:
        try:
            logging.info("Attempting to click 'Load More' button...")
            # Wait until the "Load More" button is clickable
            load_more_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Load More')]"))
            )
            load_more_button.click()
            logging.info("Clicked 'Load More' button.")
            # Wait for new jobs to load; adjust sleep time as needed
            time.sleep(3)
        except TimeoutException:
            # "Load More" button not found or not clickable; assume all jobs are loaded
            logging.info("No more 'Load More' button found. All jobs loaded.")
            break
        except Exception as e:
            logging.error(f"Error while loading more jobs: {e}")
            traceback.print_exc()
            break

# -----------------------------------------------------------------------------
# 3. Collect All Job Links
# -----------------------------------------------------------------------------
def collect_job_links(driver):
    """
    Collects all unique job links from the loaded job listings page.
    Returns a list of URLs.
    """
    job_links = []
    try:
        # Find all job card elements
        job_cards = driver.find_elements(By.CSS_SELECTOR, "div[data-testid='jobCardElement']")
        logging.info(f"Found {len(job_cards)} job cards.")
        
        for idx, job_card in enumerate(job_cards, start=1):
            try:
                # Assuming each job_card contains a link (<a> tag)
                link_element = job_card.find_element(By.TAG_NAME, "a")
                job_link = link_element.get_attribute("href")
                if job_link and job_link not in job_links:
                    job_links.append(job_link)
                    logging.info(f"Collected job link {idx}: {job_link}")
            except NoSuchElementException:
                logging.warning(f"No link found in job card {idx}. Skipping.")
            except Exception as e:
                logging.error(f"Error extracting link from job card {idx}: {e}")
                traceback.print_exc()
    except Exception as e:
        logging.error(f"Error collecting job links: {e}")
        traceback.print_exc()
    return job_links

# -----------------------------------------------------------------------------
# 4. Scrape Individual Job Details
# -----------------------------------------------------------------------------
def scrape_job_details(driver, job_link):
    """
    Navigates to the job detail page and extracts job information.
    Returns a dictionary with job details.
    """
    job_data = {
        "title": "N/A",
        "link": job_link,
        "date": "N/A",
        "description": "N/A"
    }
    try:
        driver.get(job_link)
        logging.info(f"Navigated to job detail page: {job_link}")

        # Wait until the job title is present
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "h1"))
        )
        logging.info(f"Job title found for: {job_link}")
        time.sleep(1)  # Additional wait if necessary

        soup = BeautifulSoup(driver.page_source, "html.parser")

        # Extract job title
        title_element = soup.find("h1")
        job_data["title"] = title_element.get_text(strip=True) if title_element else "N/A"

        # Extract job date by searching for the div containing "Posted on:"
        date_element = soup.find("div", string=lambda text: text and "Posted on:" in text)
        if date_element:
            raw_date = date_element.get_text(strip=True)
            job_data["date"] = parse_and_format_date(raw_date)
            logging.info(f"Extracted raw date for job '{job_data['title']}': {raw_date}")
        else:
            logging.warning(f"Date element not found for job '{job_data['title']}' at {job_link}")

        # Extract job description
        desc_element = soup.find("div", class_="ql-editor")  # Update the class if necessary
        job_data["description"] = desc_element.get_text(strip=True) if desc_element else "N/A"

        logging.info(f"Scraped job: {job_data['title']} from {job_link}")

    except TimeoutException:
        logging.error(f"Timeout while loading job detail page: {job_link}")
    except Exception as e:
        logging.error(f"Error scraping job details from {job_link}: {e}")
        traceback.print_exc()

    return job_data

def parse_and_format_date(raw_date):
    """
    Parses the raw date string and formats it as MM-DD-YYYY.
    Handles different date formats and prefixes like 'Posted on: '.
    """
    today = datetime.today().date()
    formatted_date = "N/A"

    try:
        if "Posted on:" in raw_date:
            # Remove the prefix 'Posted on: ' to isolate the date
            date_str = raw_date.replace("Posted on:", "").strip()
        else:
            date_str = raw_date.strip()

        # Handle relative dates
        if "Today" in date_str:
            formatted_date = today.strftime("%m-%d-%Y")
        elif "Yesterday" in date_str:
            yesterday = today - timedelta(days=1)
            formatted_date = yesterday.strftime("%m-%d-%Y")
        else:
            # Attempt to parse the date. Adjust the format string based on actual date format.
            # Example formats:
            # "Jan 03 2025" -> "%b %d %Y"
            # "January 03 2025" -> "%B %d %Y"
            # "01/03/2025" -> "%m/%d/%Y"
            # "01-03-2025" -> "%m-%d-%Y"
            date_formats = ["%b %d %Y", "%B %d %Y", "%m/%d/%Y", "%m-%d-%Y"]
            for fmt in date_formats:
                try:
                    parsed_date = datetime.strptime(date_str, fmt)
                    formatted_date = parsed_date.strftime("%m-%d-%Y")
                    break  # Exit the loop once parsing is successful
                except ValueError:
                    continue  # Try the next format
            else:
                # If none of the formats match, log a warning
                logging.warning(f"Unrecognized date format: {date_str}")
    except Exception as e:
        logging.error(f"Error parsing date '{raw_date}': {e}")
        traceback.print_exc()

    return formatted_date

# -----------------------------------------------------------------------------
# 5. Save results to CSV
# -----------------------------------------------------------------------------
def save_to_csv(job_entries):
    """
    Appends the newly scraped job entries to the CSV file. If the file does not
    exist, it creates one with headers.
    """
    file_exists = os.path.isfile(CSV_FILE_NAME)
    try:
        with open(CSV_FILE_NAME, mode="a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                # Write the header row only if the file is new
                writer.writerow(["Title", "Link", "Date", "Description", "Scrape Timestamp"])

            for job in job_entries:
                writer.writerow([
                    job["title"],
                    job["link"],
                    job["date"],
                    job["description"],
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ])
        logging.info(f"Saved {len(job_entries)} new job(s) to {CSV_FILE_NAME}.")
    except Exception as e:
        logging.error(f"Error saving jobs to CSV: {e}")
        traceback.print_exc()

# -----------------------------------------------------------------------------
# 6. Save Today's Jobs to External CSV
# -----------------------------------------------------------------------------
def save_todays_jobs(job_entries):
    """
    Saves jobs posted today to a separate CSV file with specified columns:
    Application URL, Name, Date posted.
    Avoids duplicates based on Application URL.
    """
    today = datetime.today().strftime("%m-%d-%Y")
    file_exists = os.path.isfile(TODAY_JOBS_FILE)
    existing_urls = set()

    if file_exists:
        try:
            with open(TODAY_JOBS_FILE, mode="r", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None)  # Skip header
                for row in reader:
                    if row:
                        existing_urls.add(row[0])  # Assuming first column is Application URL
            logging.info(f"Loaded {len(existing_urls)} existing job URLs from {TODAY_JOBS_FILE}.")
        except Exception as e:
            logging.error(f"Error reading {TODAY_JOBS_FILE}: {e}")
            traceback.print_exc()

    try:
        with open(TODAY_JOBS_FILE, mode="a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                # Write the header row only if the file is new
                writer.writerow(["Application URL", "Name", "Date Posted"])

            for job in job_entries:
                if job["link"] not in existing_urls:
                    writer.writerow([
                        job["link"],
                        job["title"],
                        job["date"]
                    ])
                    existing_urls.add(job["link"])  # Update the set to include the new URL
                    logging.info(f"Added today's job: {job['title']} ({job['link']})")
                else:
                    logging.info(f"Job already exists in {TODAY_JOBS_FILE}. Skipping: {job['link']}")
        logging.info(f"Saved {len(job_entries)} today's job(s) to {TODAY_JOBS_FILE}.")
    except Exception as e:
        logging.error(f"Error saving today's jobs to CSV: {e}")
        traceback.print_exc()

# -----------------------------------------------------------------------------
# 7. Main Execution Loop
# -----------------------------------------------------------------------------
def main():
    # Read previously scraped job titles to avoid duplicates
    previous_jobs = set()
    if os.path.isfile(CSV_FILE_NAME):
        try:
            with open(CSV_FILE_NAME, mode="r", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None)  # Skip the header
                for row in reader:
                    if row:
                        previous_jobs.add(row[0])  # Assuming first column is the title
            logging.info(f"Loaded {len(previous_jobs)} previously scraped jobs.")
        except Exception as e:
            logging.error(f"Error reading CSV file: {e}")
            traceback.print_exc()

    # Continuous monitoring for new jobs
    while True:
        driver = setup_driver()
        try:
            logging.info("Loading all jobs...")
            load_all_jobs(driver)

            logging.info("Collecting job links...")
            job_links = collect_job_links(driver)

            new_job_entries = []
            todays_job_entries = []
            for job_link in job_links:
                if job_link in [job["link"] for job in new_job_entries]:
                    logging.info(f"Duplicate job link found. Skipping: {job_link}")
                    continue  # Skip duplicate links within this run

                job_data = scrape_job_details(driver, job_link)

                if job_data["title"] not in previous_jobs:
                    new_job_entries.append(job_data)
                    previous_jobs.add(job_data["title"])

                    # Check if the job was posted today
                    if job_data["date"] == datetime.today().strftime("%m-%d-%Y"):
                        todays_job_entries.append(job_data)
                else:
                    logging.info(f"Job already exists. Skipping: {job_data['title']}")

            if new_job_entries:
                logging.info(f"Found {len(new_job_entries)} new job(s). Saving to CSV...")
                save_to_csv(new_job_entries)
            else:
                logging.info("No new jobs found.")

            if todays_job_entries:
                logging.info(f"Found {len(todays_job_entries)} job(s) posted today. Saving to {TODAY_JOBS_FILE}...")
                save_todays_jobs(todays_job_entries)
            else:
                logging.info("No jobs posted today.")

        except Exception as e:
            logging.error(f"An error occurred during scraping: {e}")
            traceback.print_exc()
        finally:
            driver.quit()

        # Wait before next check
        logging.info(f"Sleeping for {CHECK_INTERVAL} seconds...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
