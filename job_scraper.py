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

def format_job_message(jobs, source_name):
    """Format job data for Telegram message"""
    if not jobs:
        return None
    
    message = f"🚀 <b>{len(jobs)} New Jobs from {source_name}!</b>\n\n"
    
    for i, job in enumerate(jobs[:8]):  # Limit to 8 jobs per message
        message += f"📋 <b>{job['title']}</b>\n"
        message += f"🏢 {job['company']}\n"
        message += f"📍 {job['location']}\n"
        message += f"🔗 <a href='{job['url']}'>Apply Here</a>\n"
        if job.get('keywords_matched'):
            message += f"🎯 Keywords: {job['keywords_matched']}\n"
        message += "\n"
    
    if len(jobs) > 8:
        message += f"... and {len(jobs) - 8} more jobs found!\n"
    
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

def check_xing_jobs():
    """
    Bonus: Scrape XING Jobs (Austrian section)
    Returns: List of job dictionaries
    """
    logger.info("🔍 Checking XING Jobs...")
    new_jobs = []
    
    headers = {'User-Agent': get_random_user_agent()}
    
    for keyword in KEYWORDS[:3]:  # Limit keywords for XING to avoid blocking
        try:
            # XING Jobs search URL
            search_url = f"https://www.xing.com/jobs/search?keywords={quote_plus(keyword)}&location=Austria"
            
            response = requests.get(search_url, headers=headers, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # XING job selectors
                job_elements = soup.find_all('article', class_='job-card')
                
                if not job_elements:
                    job_elements = soup.find_all('div', class_='job-item')
                
                for job_element in job_elements[:3]:  # Very limited to avoid blocking
                    try:
                        title_elem = job_element.find('h3') or job_element.find('h2')
                        company_elem = job_element.find('span', class_='company-name')
                        location_elem = job_element.find('span', class_='location')
                        link_elem = job_element.find('a', href=True)
                        
                        if title_elem and link_elem:
                            title = title_elem.get_text(strip=True)
                            company = company_elem.get_text(strip=True) if company_elem else 'Unknown Company'
                            location = location_elem.get_text(strip=True) if location_elem else 'Austria'
                            
                            job_url = link_elem.get('href')
                            if job_url.startswith('/'):
                                job_url = "https://www.xing.com" + job_url
                            
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
                                    'source': 'xing.com',
                                    'keywords_matched': keywords_matched
                                }
                                new_jobs.append(job_data)
                                save_job_to_db(job_data)
                        
                    except Exception as e:
                        logger.error(f"Error parsing XING job element: {e}")
                        continue
            
            # Longer delay for XING as they're strict
            time.sleep(random.uniform(8, 12))
            
        except Exception as e:
            logger.error(f"Error checking XING for {keyword}: {e}")
            continue
    
    logger.info(f"✅ Found {len(new_jobs)} new jobs on XING")
    return new_jobs


def check_jobs_at():
    """Scrape jobs.at - Main Austrian job portal"""
    logger.info("🔍 Checking jobs.at...")
    new_jobs = []
    
    headers = {'User-Agent': get_random_user_agent()}
    
    for keyword in KEYWORDS:
        try:
            search_url = f"https://www.jobs.at/stellenangebote/{quote_plus(keyword)}"
            
            response = requests.get(search_url, headers=headers, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                job_elements = soup.find_all('article', class_='c-jobitem')
                if not job_elements:
                    job_elements = soup.find_all('div', class_='c-jobitem')
                if not job_elements:
                    job_elements = soup.find_all('div', attrs={'data-cy': 'job-item'})
                
                for job_element in job_elements[:5]:
                    try:
                        title_elem = (job_element.find('h2') or 
                                    job_element.find('h3') or 
                                    job_element.find('a', class_='jobTitle'))
                        
                        link_elem = job_element.find('a', href=True)
                        
                        if title_elem and link_elem:
                            title = title_elem.get_text(strip=True)
                            
                            company_elem = (job_element.find('span', class_='company') or
                                          job_element.find('div', class_='company') or
                                          job_element.find('span', string=lambda text: text and 'bei' in text.lower()))
                            company = company_elem.get_text(strip=True).replace('bei ', '') if company_elem else 'Unknown Company'
                            
                            location_elem = (job_element.find('span', class_='location') or
                                           job_element.find('div', class_='location'))
                            location = location_elem.get_text(strip=True) if location_elem else 'Austria'
                            
                            job_url = link_elem.get('href')
                            if job_url.startswith('/'):
                                job_url = "https://www.jobs.at" + job_url
                            elif not job_url.startswith('http'):
                                job_url = "https://www.jobs.at/" + job_url
                            
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
            
            time.sleep(random.uniform(2, 4))
            
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
    
    for keyword in KEYWORDS:
        try:
            search_url = f"https://www.karriere.at/jobs/{quote_plus(keyword)}"
            
            response = requests.get(search_url, headers=headers, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                job_elements = (soup.find_all('article', class_='m-jobItem') or 
                              soup.find_all('div', class_='jobitem') or
                              soup.find_all('div', class_='job-item'))
                
                for job_element in job_elements[:5]:
                    try:
                        title_elem = job_element.find('h2') or job_element.find('h3')
                        company_elem = (job_element.find('span', class_='company') or 
                                      job_element.find('div', class_='company'))
                        location_elem = (job_element.find('span', class_='location') or 
                                       job_element.find('div', class_='location'))
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
    
    for keyword in KEYWORDS[:5]:  # Limit to avoid being blocked
        try:
            search_url = f"https://at.indeed.com/jobs?q={quote_plus(keyword)}&l=Austria&sort=date&fromage=1"
            
            response = requests.get(search_url, headers=headers, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                job_elements = (soup.find_all('div', class_='job_seen_beacon') or 
                              soup.find_all('a', attrs={'data-jk': True}) or
                              soup.find_all('div', class_='jobsearch-SerpJobCard'))
                
                for job_element in job_elements[:3]:
                    try:
                        title_elem = (job_element.find('h2', class_='jobTitle') or 
                                    job_element.find('span', title=True) or
                                    job_element.find('a', title=True))
                        company_elem = (job_element.find('span', class_='companyName') or 
                                      job_element.find('a', attrs={'data-testid': 'company-name'}))
                        location_elem = job_element.find('div', class_='companyLocation')
                        link_elem = job_element.find('a', href=True) or job_element if job_element.name == 'a' else None
                        
                        if title_elem and link_elem:
                            title = title_elem.get_text(strip=True) or title_elem.get('title', '')
                            company = company_elem.get_text(strip=True) if company_elem else 'Unknown Company'
                            location = location_elem.get_text(strip=True) if location_elem else 'Austria'
                            
                            href = link_elem.get('href')
                            if href.startswith('/'):
                                job_url = "https://at.indeed.com" + href
                            else:
                                job_url = href
                            
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
            
            time.sleep(random.uniform(4, 6))
            
        except Exception as e:
            logger.error(f"Error checking Indeed for {keyword}: {e}")
            continue
    
    logger.info(f"✅ Found {len(new_jobs)} new jobs on Indeed")
    return new_jobs

def check_devjobs_at():
    """Scrape devjobs.at - Austrian tech job portal"""
    logger.info("🔍 Checking devjobs.at...")
    new_jobs = []
    
    headers = {'User-Agent': get_random_user_agent()}
    
    for keyword in KEYWORDS:
        try:
            search_url = f"https://devjobs.at/jobs?search={quote_plus(keyword)}&location=Austria"
            
            response = requests.get(search_url, headers=headers, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                job_elements = soup.find_all('div', class_='job-listing')
                if not job_elements:
                    job_elements = soup.find_all('article', class_='job-item')
                if not job_elements:
                    job_elements = soup.find_all('div', class_='job-card')
                
                for job_element in job_elements[:3]:
                    try:
                        title_elem = (job_element.find('h2', class_='job-title') or
                                    job_element.find('h3', class_='job-title') or
                                    job_element.find('a', class_='job-title') or
                                    job_element.find('h2') or job_element.find('h3'))
                        
                        company_elem = (job_element.find('span', class_='company-name') or
                                      job_element.find('div', class_='company') or
                                      job_element.find('p', class_='company'))
                        
                        location_elem = (job_element.find('span', class_='location') or
                                       job_element.find('div', class_='location'))
                        
                        link_elem = job_element.find('a', href=True)
                        
                        if title_elem and link_elem:
                            title = title_elem.get_text(strip=True)
                            company = company_elem.get_text(strip=True) if company_elem else 'Tech Company'
                            location = location_elem.get_text(strip=True) if location_elem else 'Austria'
                            
                            job_url = link_elem.get('href')
                            if job_url.startswith('/'):
                                job_url = "https://devjobs.at" + job_url
                            
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
                                    'source': 'devjobs.at',
                                    'keywords_matched': keywords_matched
                                }
                                new_jobs.append(job_data)
                                save_job_to_db(job_data)
                        
                    except Exception as e:
                        logger.error(f"Error parsing devjobs.at job element: {e}")
                        continue
            
            time.sleep(random.uniform(3, 5))
            
        except Exception as e:
            logger.error(f"Error checking devjobs.at for {keyword}: {e}")
            continue
    
    logger.info(f"✅ Found {len(new_jobs)} new jobs on devjobs.at")
    return new_jobs



def check_epunkt_com():
    """Scrape epunkt.com - Austrian recruitment agency"""
    logger.info("🔍 Checking epunkt.com...")
    new_jobs = []
    
    headers = {'User-Agent': get_random_user_agent()}
    
    for keyword in KEYWORDS:
        try:
            search_url = f"https://www.epunkt.com/jobs/search?q={quote_plus(keyword)}&location=Austria"
            
            response = requests.get(search_url, headers=headers, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                job_elements = soup.find_all('div', class_='job-item')
                if not job_elements:
                    job_elements = soup.find_all('article', class_='job')
                if not job_elements:
                    job_elements = soup.find_all('div', class_='position')
                
                for job_element in job_elements[:3]:
                    try:
                        title_elem = (job_element.find('h2', class_='position-title') or
                                    job_element.find('h3', class_='job-title') or
                                    job_element.find('a', class_='job-link') or
                                    job_element.find('h2') or job_element.find('h3'))
                        
                        company_elem = (job_element.find('span', class_='company') or
                                      job_element.find('div', class_='company-name') or
                                      job_element.find('p', class_='company'))
                        
                        location_elem = (job_element.find('span', class_='location') or
                                       job_element.find('div', class_='job-location'))
                        
                        link_elem = job_element.find('a', href=True)
                        
                        if title_elem and link_elem:
                            title = title_elem.get_text(strip=True)
                            company = company_elem.get_text(strip=True) if company_elem else 'Epunkt Client'
                            location = location_elem.get_text(strip=True) if location_elem else 'Austria'
                            
                            job_url = link_elem.get('href')
                            if job_url.startswith('/'):
                                job_url = "https://www.epunkt.com" + job_url
                            
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
                                    'source': 'epunkt.com',
                                    'keywords_matched': keywords_matched
                                }
                                new_jobs.append(job_data)
                                save_job_to_db(job_data)
                        
                    except Exception as e:
                        logger.error(f"Error parsing epunkt.com job element: {e}")
                        continue
            
            time.sleep(random.uniform(3, 5))
            
        except Exception as e:
            logger.error(f"Error checking epunkt.com for {keyword}: {e}")
            continue
    
    logger.info(f"✅ Found {len(new_jobs)} new jobs on epunkt.com")
    return new_jobs

# ==================== MAIN EXECUTION ====================
def main_job_check():
    """Main function to check all job sources"""
    start_time = datetime.now()
    logger.info("🚀 Starting job check cycle...")
    
    all_new_jobs = []
    
    sources = [
        ("jobs.at", check_jobs_at),
        ("karriere.at", check_karriere_at),
        ("stepstone.at", check_stepstone_at),
        ("indeed.at", check_indeed_at),
        ("devjobs.at", check_devjobs_at),
        ("epunkt.com", check_epunkt_com)
    ]
    
    for source_name, check_function in sources:
        try:
            logger.info(f"🔍 Checking {source_name}...")
            results = check_function()
            
            if results:
                all_new_jobs.extend(results)
                message = format_job_message(results, source_name)
                if message:
                    send_telegram_message(message)
                    time.sleep(2)
            
        except Exception as e:
            logger.error(f"❌ Error checking {source_name}: {e}")
            continue
    
    # Send summary message
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    if all_new_jobs:
        summary_message = f"📊 <b>Job Check Complete!</b>\n\n"
        summary_message += f"⏰ Duration: {duration:.1f} seconds\n"
        summary_message += f"🎯 Total new jobs: {len(all_new_jobs)}\n"
        summary_message += f"🔄 Next check: {(datetime.now() + timedelta(hours=2)).strftime('%H:%M')}\n\n"
        
        sources_count = {}
        for job in all_new_jobs:
            source = job['source']
            sources_count[source] = sources_count.get(source, 0) + 1
        
        summary_message += "📋 Breakdown:\n"
        for source, count in sources_count.items():
            summary_message += f"• {source}: {count} jobs\n"
        
        send_telegram_message(summary_message)
    else:
        no_jobs_message = f"😴 <b>No new jobs found</b>\n\n"
        no_jobs_message += f"⏰ Checked at: {datetime.now().strftime('%H:%M:%S')}\n"
        no_jobs_message += f"🔄 Next check: {(datetime.now() + timedelta(hours=2)).strftime('%H:%M')}\n"
        no_jobs_message += f"💪 Keep your head up! Jobs are coming!"
        
        send_telegram_message(no_jobs_message)
    
    logger.info(f"✅ Job check completed. Found {len(all_new_jobs)} new jobs in {duration:.1f} seconds")
    return len(all_new_jobs)

def send_test_message():
    """Send test message to verify Telegram setup"""
    test_message = "🤖 <b>Austrian Job Scraper Bot Activated!</b>\n\n"
    test_message += "✅ Telegram connection working\n"
    test_message += "🔍 Monitoring Austrian job portals:\n"
    test_message += "• jobs.at\n• karriere.at\n• stepstone.at\n• indeed.at\n• devjobs.at\n• epunkt.com\n\n"
    test_message += f"⏰ Checking every 2 hours\n"
    test_message += f"🎯 Monitoring {len(KEYWORDS)} keywords\n"
    test_message += f"📍 Targeting {len(LOCATIONS)} locations\n\n"
    test_message += "📋 Your keywords:\n"
    for keyword in KEYWORDS[:10]:
        test_message += f"• {keyword}\n"
    
    if len(KEYWORDS) > 10:
        test_message += f"... and {len(KEYWORDS) - 10} more\n"
    
    test_message += "\n🚀 Starting first job check in 30 seconds..."
    
    return send_telegram_message(test_message)

def send_daily_summary():
    """Send daily summary of job search activity"""
    logger.info("📊 Sending daily summary...")
    
    conn = sqlite3.connect('jobs.db')
    cursor = conn.cursor()
    
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("SELECT COUNT(*) FROM jobs WHERE date_found LIKE ?", (f"{today}%",))
    today_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM jobs")
    total_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT source, COUNT(*) FROM jobs WHERE date_found LIKE ? GROUP BY source", (f"{today}%",))
    source_breakdown = cursor.fetchall()
    
    cursor.execute("SELECT company, COUNT(*) as count FROM jobs WHERE date_found LIKE ? GROUP BY company ORDER BY count DESC LIMIT 5", (f"{today}%",))
    top_companies = cursor.fetchall()
    
    conn.close()
    
    summary = f"📊 <b>Daily Job Search Summary</b>\n\n"
    summary += f"📈 Today's new jobs: {today_count}\n"
    summary += f"🎯 Total jobs tracked: {total_count}\n\n"
    
    if source_breakdown:
        summary += "📋 Today's breakdown:\n"
        for source, count in source_breakdown:
            summary += f"• {source}: {count} jobs\n"
    
    if top_companies:
        summary += "\n🏢 Top companies today:\n"
        for company, count in top_companies:
            summary += f"• {company}: {count} jobs\n"
    
    summary += "\n🔍 Keep applying and stay motivated! 💪"
    
    send_telegram_message(summary)

def handle_telegram_commands():
    """Check for commands sent to the bot"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        response = requests.get(url)
        
        if response.status_code == 200:
            data = response.json()
            
            for update in data.get('result', []):
                if 'message' in update and 'text' in update['message']:
                    text = update['message']['text'].lower()
                    
                    if text == '/status':
                        send_status_message()
                    elif text == '/stats':
                        send_daily_summary()
                    elif text == '/check':
                        main_job_check()
                    elif text == '/help':
                        send_help_message()
    except Exception as e:
        logger.error(f"Error handling Telegram commands: {e}")

def send_status_message():
    """Send current bot status"""
    conn = sqlite3.connect('jobs.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM jobs")
    total_jobs = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM jobs WHERE date_found LIKE ?", 
                   (datetime.now().strftime('%Y-%m-%d') + '%',))
    today_jobs = cursor.fetchone()[0]
    conn.close()
    
    status_msg = f"🤖 <b>Bot Status: ACTIVE</b>\n\n"
    status_msg += f"📊 Total jobs tracked: {total_jobs}\n"
    status_msg += f"📈 Today's jobs: {today_jobs}\n"
    status_msg += f"⏰ Last check: {datetime.now().strftime('%H:%M:%S')}\n"
    status_msg += f"🔄 Next check: {(datetime.now() + timedelta(hours=2)).strftime('%H:%M')}\n\n"
    status_msg += "🔍 Monitoring sites:\n"
    status_msg += "• jobs.at\n• karriere.at\n• stepstone.at\n• indeed.at\n• devjobs.at\n• epunkt.com\n\n"
    status_msg += "📱 Available commands:\n"
    status_msg += "• /status - Bot status\n"
    status_msg += "• /stats - Daily summary\n"
    status_msg += "• /check - Manual job check\n"
    status_msg += "• /help - Command help"
    
    send_telegram_message(status_msg)

def send_help_message():
    """Send help message with available commands"""
    help_msg = f"🤖 <b>Austrian Job Scraper Bot</b>\n\n"
    help_msg += f"📋 <b>Available Commands:</b>\n\n"
    help_msg += f"🔍 /check - Run manual job search\n"
    help_msg += f"📊 /status - Show bot status & stats\n"
    help_msg += f"📈 /stats - Daily job summary\n"
    help_msg += f"❓ /help - Show this help message\n\n"
    help_msg += f"🎯 <b>Current Settings:</b>\n"
    help_msg += f"• {len(KEYWORDS)} keywords monitored\n"
    help_msg += f"• {len(LOCATIONS)} locations tracked\n"
    help_msg += f"• 6 job portals checked\n"
    help_msg += f"• Checks every 2 hours\n\n"
    help_msg += f"💡 <b>Tips:</b>\n"
    help_msg += f"• Bot runs 24/7 automatically\n"
    help_msg += f"• You'll get notified of new jobs instantly\n"
    help_msg += f"• Duplicates are automatically filtered\n"
    help_msg += f"• All jobs are saved to database"
    
    send_telegram_message(help_msg)

# ==================== SCHEDULING ====================
def start_scheduler():
    """Start the job scheduler"""
    logger.info("Starting job scheduler...")
    
    init_database()
    send_test_message()
    
    time.sleep(30)
    
    # Schedule job checks every 2 hours
    schedule.every(2).hours.do(main_job_check)
    
    # Schedule daily summary at 9 AM
    schedule.every().day.at("09:00").do(send_daily_summary)
    
    # Check for Telegram commands every 5 minutes
    schedule.every(5).minutes.do(handle_telegram_commands)
    
    # Run initial check
    main_job_check()
    
    logger.info("Scheduler started. Running continuously...")
    
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    print("🚀 Starting Austrian Job Scraper Bot...")
    print("📱 Telegram notifications enabled")
    print("🔍 Monitoring 6 job portals every 2 hours")
    print("⏹️  Press Ctrl+C to stop")
    
    try:
        start_scheduler()
    except KeyboardInterrupt:
        print("\n🛑 Job scraper stopped by user")
        logger.info("Job scraper stopped by user")
    except Exception as e:
        print(f"❌ Error: {e}")
        logger.error(f"Fatal error: {e}")