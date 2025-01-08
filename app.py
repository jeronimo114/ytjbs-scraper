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
URL = "https://ytjobs.co/job/search/video_editor"           # The job listings page
CHECK_INTERVAL = 1200                 # Check for new jobs every X seconds
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

    service = Service(ChromeDriverManager().install())

    driver = webdriver.Chrome(service=service, options=chrome_options)
    logger.info("WebDriver initialized and navigating to the job listings page.")
    driver.get(URL)
    return driver

# -----------------------------------------------------------------------------
# 2a. Try to click a "See More" button repeatedly (max 5 times)
# -----------------------------------------------------------------------------
def click_see_more_button(driver, max_clicks=5):
    """
    Clicks the 'See More' button repeatedly until it no longer appears,
    or until max_clicks times (to prevent infinite loops).
    """
    clicks = 0
    while clicks < max_clicks:
        try:
            logger.info(f"Attempting to click 'See More' button (Attempt {clicks + 1}/{max_clicks})...")
            # Update the XPath to match 'See More' instead of 'Load More'
            see_more_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'See More')]"))
            )
            see_more_button.click()
            logger.info("Clicked 'See More' button.")
            # Wait for new jobs to load
            time.sleep(3)
            clicks += 1
        except TimeoutException:
            logger.info("No 'See More' button found or it is no longer clickable.")
            break
        except ElementClickInterceptedException:
            logger.warning("ElementClickInterceptedException encountered. Trying to click again after a short wait.")
            time.sleep(2)
        except Exception as e:
            logger.error(f"Error while clicking 'See More': {e}")
            traceback.print_exc()
            break

# -----------------------------------------------------------------------------
# 2b. Perform infinite scroll (handles lazy loading and “scroll-to-load”)
# -----------------------------------------------------------------------------
def scroll_to_load_all(driver, pause_time=2, max_scrolls=20):
    """
    Scrolls to the bottom of the page repeatedly, allowing lazy-loaded elements
    to appear. Continues until the page height stops changing or until we 
    reach max_scrolls attempts.
    """
    last_height = driver.execute_script("return document.body.scrollHeight")
    scroll_attempts = 0

    while scroll_attempts < max_scrolls:
        # Scroll down
        logger.info(f"Scrolling to bottom (Scroll attempt {scroll_attempts + 1}/{max_scrolls})...")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause_time)

        # Calculate new scroll height
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            logger.info("Page height did not change after scrolling. Assuming all jobs loaded.")
            break
        last_height = new_height
        scroll_attempts += 1

# -----------------------------------------------------------------------------
# 2c. Main function to load all jobs
# -----------------------------------------------------------------------------
def load_all_jobs(driver):
    """
    Clicks the 'See More' button up to 5 times, then performs infinite scroll.
    """
    # 1) Click the "See More" button up to 5 times
    click_see_more_button(driver, max_clicks=5)

    # 2) Then perform infinite scroll to catch any additional lazy-loaded items
    scroll_to_load_all(driver, pause_time=2, max_scrolls=20)

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
        # Update the CSS selector if necessary
        job_cards = driver.find_elements(By.CSS_SELECTOR, "div[data-testid='jobCardElement']")
        logger.info(f"Found {len(job_cards)} job cards on the page.")

        for idx, job_card in enumerate(job_cards, start=1):
            try:
                link_element = job_card.find_element(By.TAG_NAME, "a")
                job_link = link_element.get_attribute("href")
                if job_link and job_link not in job_links:
                    job_links.append(job_link)
                    logger.info(f"Collected job link {idx}: {job_link}")
            except NoSuchElementException:
                logger.warning(f"No link found in job card {idx}. Skipping.")
            except Exception as e:
                logger.error(f"Error extracting link from job card {idx}: {e}")
                traceback.print_exc()
    except Exception as e:
        logger.error(f"Error collecting job links: {e}")
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
        logger.info(f"Navigated to job detail page: {job_link}")

        # Wait until the job title is present
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "h1"))
        )
        logger.info(f"Job title found for: {job_link}")
        time.sleep(1)  # Additional short wait

        soup = BeautifulSoup(driver.page_source, "html.parser")

        # Extract job title
        title_element = soup.find("h1")
        if title_element:
            job_data["title"] = title_element.get_text(strip=True)

        # Extract job date (looking for "Posted on:")
        # Adjust as needed if the site’s HTML changes
        posted_on_element = soup.find("div", string=lambda text: text and "Posted on:" in text)
        if posted_on_element:
            raw_date = posted_on_element.get_text(strip=True)
            job_data["date"] = parse_and_format_date(raw_date)
            logger.info(f"Extracted raw date for job '{job_data['title']}': {raw_date}")
        else:
            logger.warning(f"Date element not found for job '{job_data['title']}' at {job_link}")

        # Extract job description
        desc_element = soup.find("div", class_="ql-editor")  # Update if class changed
        if desc_element:
            job_data["description"] = desc_element.get_text(strip=True)

        logger.info(f"Scraped job: {job_data['title']} from {job_link}")

    except TimeoutException:
        logger.error(f"Timeout while loading job detail page: {job_link}")
    except Exception as e:
        logger.error(f"Error scraping job details from {job_link}: {e}")
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
        # Remove "Posted on:" if present
        if "Posted on:" in raw_date:
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
            # Try multiple known date formats
            date_formats = ["%b %d %Y", "%B %d %Y", "%m/%d/%Y", "%m-%d-%Y"]
            for fmt in date_formats:
                try:
                    parsed_date = datetime.strptime(date_str, fmt)
                    formatted_date = parsed_date.strftime("%m-%d-%Y")
                    break
                except ValueError:
                    continue
            else:
                logger.warning(f"Unrecognized date format: {date_str}")
    except Exception as e:
        logger.error(f"Error parsing date '{raw_date}': {e}")
        traceback.print_exc()

    return formatted_date

# -----------------------------------------------------------------------------
# 5. Save results to CSV
# -----------------------------------------------------------------------------
def save_to_csv(job_entries):
    """
    Appends the newly scraped job entries to the CSV file. 
    If the file does not exist, it creates one with headers.
    """
    file_exists = os.path.isfile(CSV_FILE_NAME)
    try:
        with open(CSV_FILE_NAME, mode="a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                # Write header only if new file
                writer.writerow(["Title", "Link", "Date", "Description", "Scrape Timestamp"])

            for job in job_entries:
                writer.writerow([
                    job["title"],
                    job["link"],
                    job["date"],
                    job["description"],
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ])
        logger.info(f"Saved {len(job_entries)} new job(s) to {CSV_FILE_NAME}.")
    except Exception as e:
        logger.error(f"Error saving jobs to CSV: {e}")
        traceback.print_exc()

# -----------------------------------------------------------------------------
# 6. Save Today's Jobs to External CSV
# -----------------------------------------------------------------------------
def save_todays_jobs(job_entries):
    """
    Saves jobs posted today to a separate CSV file with columns:
    [Application URL, Name, Date Posted].
    Avoids duplicates based on Application URL.
    """
    today = datetime.today().strftime("%m-%d-%Y")
    file_exists = os.path.isfile(TODAY_JOBS_FILE)
    existing_urls = set()

    if file_exists:
        try:
            with open(TODAY_JOBS_FILE, mode="r", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None)  # skip header
                for row in reader:
                    if row:
                        existing_urls.add(row[0])  # first column is URL
            logger.info(f"Loaded {len(existing_urls)} existing job URLs from {TODAY_JOBS_FILE}.")
        except Exception as e:
            logger.error(f"Error reading {TODAY_JOBS_FILE}: {e}")
            traceback.print_exc()

    try:
        with open(TODAY_JOBS_FILE, mode="a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Application URL", "Name", "Date Posted"])

            for job in job_entries:
                if job["link"] not in existing_urls:
                    writer.writerow([
                        job["link"],
                        job["title"],
                        job["date"]
                    ])
                    existing_urls.add(job["link"])
                    logger.info(f"Added today's job: {job['title']} ({job['link']})")
                else:
                    logger.info(f"Already in today's file: {job['title']} ({job['link']})")
        logger.info(f"Saved {len(job_entries)} job(s) posted today to {TODAY_JOBS_FILE}.")
    except Exception as e:
        logger.error(f"Error saving today's jobs to CSV: {e}")
        traceback.print_exc()

# -----------------------------------------------------------------------------
# 7. Main Execution Loop
# -----------------------------------------------------------------------------
def main():
    # Load previously scraped jobs (by Title) to avoid duplicates
    previous_jobs = set()
    if os.path.isfile(CSV_FILE_NAME):
        try:
            with open(CSV_FILE_NAME, mode="r", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None)  # skip header
                for row in reader:
                    if row:
                        previous_jobs.add(row[0])  # first column is the title
            logger.info(f"Loaded {len(previous_jobs)} previously scraped jobs.")
        except Exception as e:
            logger.error(f"Error reading {CSV_FILE_NAME}: {e}")
            traceback.print_exc()

    while True:
        driver = setup_driver()
        try:
            logger.info("Loading all jobs...")
            load_all_jobs(driver)

            logger.info("Collecting job links...")
            job_links = collect_job_links(driver)

            new_job_entries = []
            todays_job_entries = []
            today_str = datetime.today().strftime("%m-%d-%Y")

            for job_link in job_links:
                # Avoid re-scraping the same link within a single run
                # or duplicates within this iteration
                if job_link in [j["link"] for j in new_job_entries]:
                    continue

                job_data = scrape_job_details(driver, job_link)

                # Check if we already have this job (by title) from previous runs
                if job_data["title"] not in previous_jobs:
                    new_job_entries.append(job_data)
                    previous_jobs.add(job_data["title"])

                    # If date == today's date, also store in today's file
                    if job_data["date"] == today_str:
                        todays_job_entries.append(job_data)
                else:
                    logger.info(f"Job already exists. Skipping: {job_data['title']}")

            if new_job_entries:
                logger.info(f"Found {len(new_job_entries)} new job(s). Saving to CSV...")
                save_to_csv(new_job_entries)
            else:
                logger.info("No new jobs found.")

            if todays_job_entries:
                logger.info(
                    f"Found {len(todays_job_entries)} job(s) posted today. Saving to {TODAY_JOBS_FILE}..."
                )
                save_todays_jobs(todays_job_entries)
            else:
                logger.info("No jobs posted today.")

        except Exception as e:
            logger.error(f"An error occurred during scraping: {e}")
            traceback.print_exc()
        finally:
            driver.quit()

        logger.info(f"Sleeping for {CHECK_INTERVAL} seconds...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
