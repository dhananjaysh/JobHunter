import requests
import time
import json
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import schedule
import feedparser
import os
import sqlite3
from urllib.parse import urlencode, quote_plus
import logging
import random

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================
TELEGRAM_TOKEN = "7884203842:AAHukVcyxUnfERt0_WqrvUmXzLEijutJT-E"
CHAT_ID = "578228757"

# ==================== JOB SEARCH PARAMETERS ====================
KEYWORDS = [
    "data scientist", "data analyst", "machine learning", "ML engineer",
    "mechanical engineer", "process engineer", "automation engineer", 
    "python developer", "software engineer", "business intelligence",
    "werkstudent", "praktikum", "internship", "junior engineer",
    "data engineer", "AI engineer", "robotics engineer", "IoT"
]

LOCATIONS = ["Vienna", "Wien", "Salzburg", "Graz", "Linz", "Innsbruck", "Austria", "Lower Austria", "Upper Austria", "österreich",
            "Steiermark", "Tyrol", "Vorarlberg", "Burgenland", "Carinthia"]

EXCLUDE_KEYWORDS = [
    "senior", "lead", "manager", "director", "head of", 
    "PhD required", "5+ years", "10+ years", "expert level"
]

# ==================== DATABASE FUNCTIONS ====================
def init_database():
    """Initialize SQLite database to store job postings and avoid duplicates"""
    conn = sqlite3.connect('jobs.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            company TEXT,
            location TEXT,
            url TEXT UNIQUE,
            posted_date TEXT,
            source TEXT,
            date_found TEXT,
            keywords_matched TEXT
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("Database initialized")

def is_job_already_sent(job_url):
    """Check if job was already sent to avoid duplicates"""
    conn = sqlite3.connect('jobs.db')
    cursor = conn.cursor()
    cursor.execute('SELECT url FROM jobs WHERE url = ?', (job_url,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def save_job_to_db(job_data):
    """Save job data to database"""
    conn = sqlite3.connect('jobs.db')
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO jobs (title, company, location, url, posted_date, source, date_found, keywords_matched)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            job_data['title'],
            job_data['company'],
            job_data['location'],
            job_data['url'],
            job_data['posted_date'],
            job_data['source'],
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            job_data.get('keywords_matched', '')
        ))
        conn.commit()
        logger.info(f"Saved job to database: {job_data['title']}")
    except sqlite3.IntegrityError:
        pass  # Job already exists
    finally:
        conn.close()

# ==================== TELEGRAM FUNCTIONS ====================
def send_telegram_message(message):
    """Send message to Telegram using requests"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        
        # Split long messages
        if len(message) > 4096:
            for i in range(0, len(message), 4096):
                payload = {
                    'chat_id': CHAT_ID,
                    'text': message[i:i+4096],
                    'parse_mode': 'HTML'
                }
                response = requests.post(url, json=payload)
                time.sleep(1)
        else:
            payload = {
                'chat_id': CHAT_ID,
                'text': message,
                'parse_mode': 'HTML'
            }
            response = requests.post(url, json=payload)
            
        if response.status_code == 200:
            logger.info("Telegram message sent successfully")
            return True
        else:
            logger.error(f"Failed to send Telegram message: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")
        return False

def format_consolidated_job_message(all_jobs):
    """Format ALL jobs from ALL sources into ONE message"""
    if not all_jobs:
        return None
    
    # Group jobs by source
    jobs_by_source = {}
    for job in all_jobs:
        source = job['source']
        if source not in jobs_by_source:
            jobs_by_source[source] = []
        jobs_by_source[source].append(job)
    
    message = f"🚀 <b>{len(all_jobs)} New Jobs Found!</b>\n\n"
    
    # Show breakdown by source
    for source, jobs in jobs_by_source.items():
        message += f"📋 <b>{source.upper()}</b> ({len(jobs)} jobs)\n"
        for job in jobs[:3]:  # Show max 3 jobs per source to keep message manageable
            message += f"• {job['title']} at {job['company']}\n"
            message += f"  📍 {job['location']}\n"
            message += f"  🔗 <a href='{job['url']}'>Apply</a>\n"
        if len(jobs) > 3:
            message += f"  ... and {len(jobs) - 3} more\n"
        message += "\n"
    
    message += f"⏰ Found at: {datetime.now().strftime('%H:%M:%S')}"
    
    return message

# ==================== UTILITY FUNCTIONS ====================
def get_random_user_agent():
    """Return random user agent to avoid detection"""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0'
    ]
    return random.choice(user_agents)

def should_exclude_job(title, description=""):
    """Check if job should be excluded based on keywords"""
    text_to_check = f"{title} {description}".lower()
    for exclude_keyword in EXCLUDE_KEYWORDS:
        if exclude_keyword.lower() in text_to_check:
            return True
    return False

def find_matching_keywords(title, description=""):
    """Find which keywords matched this job"""
    text_to_check = f"{title} {description}".lower()
    matched = []
    for keyword in KEYWORDS:
        if keyword.lower() in text_to_check:
            matched.append(keyword)
    return ", ".join(matched[:3])  # Limit to 3 keywords

# ==================== JOB SCRAPING FUNCTIONS ====================

def check_jobs_at():
    """Scrape jobs.at - Main Austrian job portal"""
    logger.info("🔍 Checking jobs.at...")
    new_jobs = []
    
    headers = {'User-Agent': get_random_user_agent()}
    
    # Only check first 3 keywords to avoid being blocked
    for keyword in KEYWORDS[:3]:
        try:
            search_url = f"https://www.jobs.at/stellenangebote/{quote_plus(keyword)}"
            
            response = requests.get(search_url, headers=headers, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                job_elements = soup.find_all('article', class_='c-jobitem')
                if not job_elements:
                    job_elements = soup.find_all('div', class_='c-jobitem')
                
                for job_element in job_elements[:2]:  # Only 2 jobs per keyword
                    try:
                        title_elem = job_element.find('h2') or job_element.find('h3')
                        link_elem = job_element.find('a', href=True)
                        
                        if title_elem and link_elem:
                            title = title_elem.get_text(strip=True)
                            
                            company_elem = job_element.find('span', class_='company')
                            company = company_elem.get_text(strip=True) if company_elem else 'Unknown Company'
                            
                            location_elem = job_element.find('span', class_='location')
                            location = location_elem.get_text(strip=True) if location_elem else 'Austria'
                            
                            job_url = link_elem.get('href')
                            if job_url.startswith('/'):
                                job_url = "https://www.jobs.at" + job_url
                            
                            if should_exclude_job(title):
                                continue
                            
                            if not is_job_already_sent(job_url):
                                keywords_matched = find_matching_keywords(title)
                                
                                job_data = {
                                    'title': title,
                                    'company': company,
                                    'location': location,
                                    'url': job_url,
                                    'posted_date': 'Today',
                                    'source': 'jobs.at',
                                    'keywords_matched': keywords_matched
                                }
                                new_jobs.append(job_data)
                                save_job_to_db(job_data)
                        
                    except Exception as e:
                        logger.error(f"Error parsing job element: {e}")
                        continue
            
            time.sleep(random.uniform(3, 5))
            
        except Exception as e:
            logger.error(f"Error checking jobs.at for {keyword}: {e}")
            continue
    
    logger.info(f"✅ Found {len(new_jobs)} new jobs on jobs.at")
    return new_jobs

def check_karriere_at():
    """Scrape karriere.at - Austrian career portal"""
    logger.info("🔍 Checking karriere.at...")
    new_jobs = []
    
    headers = {'User-Agent': get_random_user_agent()}
    
    for keyword in KEYWORDS[:3]:  # Limit keywords
        try:
            search_url = f"https://www.karriere.at/jobs/{quote_plus(keyword)}"
            
            response = requests.get(search_url, headers=headers, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                job_elements = soup.find_all('article', class_='m-jobItem')
                
                for job_element in job_elements[:2]:  # Only 2 jobs per keyword
                    try:
                        title_elem = job_element.find('h2') or job_element.find('h3')
                        company_elem = job_element.find('span', class_='company')
                        location_elem = job_element.find('span', class_='location')
                        link_elem = job_element.find('a', href=True)
                        
                        if title_elem and link_elem:
                            title = title_elem.get_text(strip=True)
                            company = company_elem.get_text(strip=True) if company_elem else 'Unknown Company'
                            location = location_elem.get_text(strip=True) if location_elem else 'Austria'
                            
                            job_url = link_elem.get('href')
                            if job_url.startswith('/'):
                                job_url = "https://www.karriere.at" + job_url
                            
                            if should_exclude_job(title):
                                continue
                            
                            if not is_job_already_sent(job_url):
                                keywords_matched = find_matching_keywords(title)
                                
                                job_data = {
                                    'title': title,
                                    'company': company,
                                    'location': location,
                                    'url': job_url,
                                    'posted_date': 'Today',
                                    'source': 'karriere.at',
                                    'keywords_matched': keywords_matched
                                }
                                new_jobs.append(job_data)
                                save_job_to_db(job_data)
                        
                    except Exception as e:
                        logger.error(f"Error parsing job element: {e}")
                        continue
            
            time.sleep(random.uniform(3, 5))
            
        except Exception as e:
            logger.error(f"Error checking karriere.at for {keyword}: {e}")
            continue
    
    logger.info(f"✅ Found {len(new_jobs)} new jobs on karriere.at")
    return new_jobs

def check_indeed_at():
    """Scrape Indeed Austria"""
    logger.info("🔍 Checking Indeed Austria...")
    new_jobs = []
    
    headers = {'User-Agent': get_random_user_agent()}
    
    for keyword in KEYWORDS[:2]:  # Very limited to avoid blocking
        try:
            search_url = f"https://at.indeed.com/jobs?q={quote_plus(keyword)}&l=Austria&sort=date&fromage=1"
            
            response = requests.get(search_url, headers=headers, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                job_elements = soup.find_all('div', class_='job_seen_beacon')
                
                for job_element in job_elements[:2]:  # Only 2 jobs per keyword
                    try:
                        title_elem = job_element.find('h2', class_='jobTitle')
                        company_elem = job_element.find('span', class_='companyName')
                        location_elem = job_element.find('div', class_='companyLocation')
                        link_elem = job_element.find('a', href=True)
                        
                        if title_elem and link_elem:
                            title = title_elem.get_text(strip=True)
                            company = company_elem.get_text(strip=True) if company_elem else 'Unknown Company'
                            location = location_elem.get_text(strip=True) if location_elem else 'Austria'
                            
                            href = link_elem.get('href')
                            job_url = "https://at.indeed.com" + href if href.startswith('/') else href
                            
                            if should_exclude_job(title):
                                continue
                            
                            if not is_job_already_sent(job_url):
                                keywords_matched = find_matching_keywords(title)
                                
                                job_data = {
                                    'title': title,
                                    'company': company,
                                    'location': location,
                                    'url': job_url,
                                    'posted_date': 'Recent',
                                    'source': 'indeed.at',
                                    'keywords_matched': keywords_matched
                                }
                                new_jobs.append(job_data)
                                save_job_to_db(job_data)
                        
                    except Exception as e:
                        logger.error(f"Error parsing Indeed job element: {e}")
                        continue
            
            time.sleep(random.uniform(5, 8))
            
        except Exception as e:
            logger.error(f"Error checking Indeed for {keyword}: {e}")
            continue
    
    logger.info(f"✅ Found {len(new_jobs)} new jobs on Indeed")
    return new_jobs

def check_linkedin_jobs():
    """Scrape LinkedIn Jobs for Austria - Simplified version"""
    logger.info("🔍 Checking LinkedIn Jobs...")
    new_jobs = []
    
    headers = {
        'User-Agent': get_random_user_agent(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }
    
    # Only use 2 keywords for LinkedIn to avoid blocking
    for keyword in KEYWORDS[:2]:
        try:
            search_url = f"https://www.linkedin.com/jobs/search?keywords={quote_plus(keyword)}&location=Austria&f_TPR=r86400"
            
            response = requests.get(search_url, headers=headers, timeout=20)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                job_elements = soup.find_all('div', class_='base-card')[:2]  # Only 2 jobs per keyword
                
                for job_element in job_elements:
                    try:
                        title_elem = job_element.find('h3', class_='base-search-card__title')
                        company_elem = job_element.find('h4', class_='base-search-card__subtitle')
                        location_elem = job_element.find('span', class_='job-search-card__location')
                        link_elem = job_element.find('a', class_='base-card__full-link')
                        
                        if title_elem and link_elem:
                            title = title_elem.get_text(strip=True)
                            company = company_elem.get_text(strip=True) if company_elem else 'LinkedIn Company'
                            location = location_elem.get_text(strip=True) if location_elem else 'Austria'
                            job_url = link_elem.get('href')
                            
                            # Clean up text
                            title = ' '.join(title.split())
                            company = ' '.join(company.split())
                            location = ' '.join(location.split())
                            
                            if should_exclude_job(title):
                                continue
                            
                            if job_url and not is_job_already_sent(job_url):
                                keywords_matched = find_matching_keywords(title)
                                
                                job_data = {
                                    'title': title,
                                    'company': company,
                                    'location': location,
                                    'url': job_url,
                                    'posted_date': 'Recent',
                                    'source': 'linkedin.com',
                                    'keywords_matched': keywords_matched
                                }
                                new_jobs.append(job_data)
                                save_job_to_db(job_data)
                        
                    except Exception as e:
                        logger.error(f"Error parsing LinkedIn job element: {e}")
                        continue
            
            elif response.status_code == 429:
                logger.warning("LinkedIn rate limit hit")
                break
            
            time.sleep(random.uniform(10, 15))  # Longer delay for LinkedIn
            
        except Exception as e:
            logger.error(f"Error checking LinkedIn for {keyword}: {e}")
            continue
    
    logger.info(f"✅ Found {len(new_jobs)} new jobs on LinkedIn")
    return new_jobs

# ==================== MAIN EXECUTION (FIXED) ====================
def main_job_check():
    """Main function to check all job sources - SINGLE MESSAGE ONLY"""
    start_time = datetime.now()
    logger.info("🚀 Starting job check cycle...")
    
    all_new_jobs = []  # Collect ALL jobs from ALL sources
    
    # Define sources to check
    sources = [
        ("jobs.at", check_jobs_at),
        ("karriere.at", check_karriere_at),
        ("indeed.at", check_indeed_at),
        ("linkedin.com", check_linkedin_jobs)
    ]
    
    # Check all sources and collect jobs
    for source_name, check_function in sources:
        try:
            logger.info(f"🔍 Checking {source_name}...")
            results = check_function()
            
            if results:
                all_new_jobs.extend(results)  # Add to the master list
                logger.info(f"Found {len(results)} jobs from {source_name}")
            
        except Exception as e:
            logger.error(f"❌ Error checking {source_name}: {e}")
            continue
    
    # Send ONE consolidated message with ALL jobs
    if all_new_jobs:
        message = format_consolidated_job_message(all_new_jobs)
        if message:
            send_telegram_message(message)
            logger.info(f"📱 Sent single message with {len(all_new_jobs)} jobs")
    else:
        # Only send "no jobs" message if nothing found
        no_jobs_message = f"😴 <b>No new jobs found</b>\n\n"
        no_jobs_message += f"⏰ Checked at: {datetime.now().strftime('%H:%M:%S')}\n"
        no_jobs_message += f"🔄 Next check: {(datetime.now() + timedelta(hours=2)).strftime('%H:%M')}"
        
        send_telegram_message(no_jobs_message)
    
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    logger.info(f"✅ Job check completed. Found {len(all_new_jobs)} new jobs in {duration:.1f} seconds")
    return len(all_new_jobs)

# ==================== SCHEDULING (SIMPLIFIED) ====================
def start_scheduler():
    """Start the job scheduler - simplified version"""
    logger.info("Starting job scheduler...")
    
    init_database()
    
    # NO TEST MESSAGE - Start directly
    logger.info("Bot started - checking jobs in 30 seconds...")
    
    # Wait before first check
    time.sleep(30)
    
    # Schedule job checks every 2 hours
    schedule.every(2).hours.do(main_job_check)
    
    # Run initial check
    main_job_check()
    
    logger.info("Scheduler started. Running continuously...")
    
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    print("🚀 Starting Simplified Job Scraper...")
    print("📱 Single Telegram notification per cycle")
    print("🔍 Checking 4 job portals every 2 hours")
    print("⏹️  Press Ctrl+C to stop")
    
    try:
        start_scheduler()
    except KeyboardInterrupt:
        print("\n🛑 Job scraper stopped by user")
        logger.info("Job scraper stopped by user")
    except Exception as e:
        print(f"❌ Error: {e}")
        logger.error(f"Fatal error: {e}")