import requests
import time
import json
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import schedule
import feedparser
import pandas as pd
import os
import sqlite3
from urllib.parse import urlencode, quote_plus
import logging
import random

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== YOUR CONFIGURATION ====================
TELEGRAM_TOKEN = "7884203842:AAHukVcyxUnfERt0_WqrvUmXzLEijutJT-E"
CHAT_ID = "578228757"

# ==================== JOB SEARCH PARAMETERS ====================
KEYWORDS = [
    "data scientist", "data analyst", "machine learning", "ML engineer",
    "mechanical engineer", "process engineer", "automation engineer", 
    "python developer", "software engineer", "business intelligence",
    "werkstudent", "praktikum", "internship", "junior engineer",
    "data engineer", "AI engineer", "robotics engineer", "SQL", "IoT"
]

LOCATIONS = ["Vienna", "Wien", "Salzburg", "Graz", "Linz", "Innsbruck", "Austria","Lower Austria", "Upper Austria", "Styria", "Tyrol", "Vorarlberg", "Carinthia", "Burgenland"]

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
    """Send message to Telegram using requests (fixed async issue)"""
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
                if response.status_code == 200:
                    logger.info("Telegram message sent successfully")
                else:
                    logger.error(f"Failed to send Telegram message: {response.text}")
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
                
                # Look for job listings with multiple selectors
                job_elements = soup.find_all('article', class_='c-jobitem')
                if not job_elements:
                    job_elements = soup.find_all('div', class_='c-jobitem')
                if not job_elements:
                    job_elements = soup.find_all('div', attrs={'data-cy': 'job-item'})
                
                for job_element in job_elements[:5]:  # Limit to first 5 per keyword
                    try:
                        # Extract job information with multiple selectors
                        title_elem = (job_element.find('h2') or 
                                    job_element.find('h3') or 
                                    job_element.find('a', class_='jobTitle'))
                        
                        link_elem = job_element.find('a', href=True)
                        
                        if title_elem and link_elem:
                            title = title_elem.get_text(strip=True)
                            
                            # Try to find company
                            company_elem = (job_element.find('span', class_='company') or
                                          job_element.find('div', class_='company') or
                                          job_element.find('span', string=lambda text: text and 'bei' in text.lower()))
                            company = company_elem.get_text(strip=True).replace('bei ', '') if company_elem else 'Unknown Company'
                            
                            # Try to find location
                            location_elem = (job_element.find('span', class_='location') or
                                           job_element.find('div', class_='location'))
                            location = location_elem.get_text(strip=True) if location_elem else 'Austria'
                            
                            # Construct full URL
                            job_url = link_elem.get('href')
                            if job_url.startswith('/'):
                                job_url = "https://www.jobs.at" + job_url
                            elif not job_url.startswith('http'):
                                job_url = "https://www.jobs.at/" + job_url
                            
                            # Check if job should be excluded
                            if should_exclude_job(title):
                                continue
                            
                            # Check if job is already in database
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
            
            # Rate limiting
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
                
                # Find job listings with multiple selectors
                job_elements = (soup.find_all('article', class_='m-jobItem') or 
                              soup.find_all('div', class_='jobitem') or
                              soup.find_all('div', class_='job-item'))
                
                for job_element in job_elements[:5]:  # Limit per keyword
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

def check_stepstone_at():
    """Check StepStone.at using RSS feeds"""
    logger.info("🔍 Checking stepstone.at...")
    new_jobs = []
    
    for keyword in KEYWORDS:
        try:
            rss_url = f"https://www.stepstone.at/jobs/rss?what={quote_plus(keyword)}&where=Austria"
            
            feed = feedparser.parse(rss_url)
            
            for entry in feed.entries[:3]:  # Limit per keyword
                try:
                    title = entry.title
                    company = getattr(entry, 'author', 'Unknown Company')
                    location = 'Austria'
                    job_url = entry.link
                    posted_date = getattr(entry, 'published', 'Unknown')
                    
                    # Extract location from description if available
                    if hasattr(entry, 'summary'):
                        summary = entry.summary.lower()
                        for loc in LOCATIONS:
                            if loc.lower() in summary:
                                location = loc
                                break
                    
                    if should_exclude_job(title):
                        continue
                    
                    if not is_job_already_sent(job_url):
                        keywords_matched = find_matching_keywords(title)
                        
                        job_data = {
                            'title': title,
                            'company': company,
                            'location': location,
                            'url': job_url,
                            'posted_date': posted_date,
                            'source': 'stepstone.at',
                            'keywords_matched': keywords_matched
                        }
                        new_jobs.append(job_data)
                        save_job_to_db(job_data)
                    
                except Exception as e:
                    logger.error(f"Error parsing RSS entry: {e}")
                    continue
            
            time.sleep(random.uniform(2, 3))
            
        except Exception as e:
            logger.error(f"Error checking stepstone.at RSS for {keyword}: {e}")
            continue
    
    logger.info(f"✅ Found {len(new_jobs)} new jobs on stepstone.at")
    return new_jobs



def check_devjobs_at():
    """
    Scrape devjobs.at - Austrian tech job portal
    Returns: List of job dictionaries
    """
    logger.info("🔍 Checking devjobs.at...")
    new_jobs = []
    
    headers = {'User-Agent': get_random_user_agent()}
    
    for keyword in KEYWORDS:
        try:
            # devjobs.at search URL structure
            search_url = f"https://devjobs.at/jobs?search={quote_plus(keyword)}&location=Austria"
            
            response = requests.get(search_url, headers=headers, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Find job listings - devjobs.at specific selectors
                job_elements = soup.find_all('div', class_='job-listing')
                
                # Alternative selectors if the above doesn't work
                if not job_elements:
                    job_elements = soup.find_all('article', class_='job-item')
                    
                if not job_elements:
                    job_elements = soup.find_all('div', class_='job-card')
                
                # If still no elements, try more generic approach
                if not job_elements:
                    job_elements = soup.find_all('div', attrs={'data-job-id': True})
                
                for job_element in job_elements[:5]:  # Limit per keyword
                    try:
                        # Extract job information with multiple fallback selectors
                        title_elem = (
                            job_element.find('h2', class_='job-title') or
                            job_element.find('h3', class_='job-title') or
                            job_element.find('a', class_='job-title') or
                            job_element.find('h2') or
                            job_element.find('h3')
                        )
                        
                        # Find company with multiple selectors
                        company_elem = (
                            job_element.find('span', class_='company-name') or
                            job_element.find('div', class_='company') or
                            job_element.find('p', class_='company') or
                            job_element.find('span', class_='employer')
                        )
                        
                        # Find location
                        location_elem = (
                            job_element.find('span', class_='location') or
                            job_element.find('div', class_='location') or
                            job_element.find('span', class_='job-location')
                        )
                        
                        # Find link
                        link_elem = (
                            job_element.find('a', href=True) or
                            title_elem.find('a', href=True) if title_elem else None
                        )
                        
                        if title_elem and link_elem:
                            title = title_elem.get_text(strip=True)
                            company = company_elem.get_text(strip=True) if company_elem else 'Unknown Company'
                            location = location_elem.get_text(strip=True) if location_elem else 'Austria'
                            
                            # Construct full URL
                            job_url = link_elem.get('href')
                            if job_url.startswith('/'):
                                job_url = "https://devjobs.at" + job_url
                            elif not job_url.startswith('http'):
                                job_url = "https://devjobs.at/" + job_url
                            
                            # Clean up extracted text
                            title = title.replace('\n', ' ').replace('\t', ' ').strip()
                            company = company.replace('\n', ' ').replace('\t', ' ').strip()
                            location = location.replace('\n', ' ').replace('\t', ' ').strip()
                            
                            # Check if job should be excluded
                            if should_exclude_job(title):
                                continue
                            
                            # Check if job is already in database
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
                                
                                logger.info(f"Found job: {title} at {company}")
                        
                    except Exception as e:
                        logger.error(f"Error parsing devjobs.at job element: {e}")
                        continue
            
            else:
                logger.warning(f"devjobs.at returned status code: {response.status_code}")
            
            # Rate limiting - be respectful
            time.sleep(random.uniform(3, 5))
            
        except Exception as e:
            logger.error(f"Error checking devjobs.at for {keyword}: {e}")
            continue
    
    logger.info(f"✅ Found {len(new_jobs)} new jobs on devjobs.at")
    return new_jobs


def check_epunkt_com():
    """
    Scrape epunkt.com - Austrian recruitment agency
    Returns: List of job dictionaries
    """
    logger.info("🔍 Checking epunkt.com...")
    new_jobs = []
    
    headers = {'User-Agent': get_random_user_agent()}
    
    for keyword in KEYWORDS:
        try:
            # epunkt.com search URL structure
            search_url = f"https://www.epunkt.com/jobs/search?q={quote_plus(keyword)}&location=Austria"
            
            response = requests.get(search_url, headers=headers, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Find job listings - epunkt.com specific selectors
                job_elements = soup.find_all('div', class_='job-item')
                
                # Alternative selectors
                if not job_elements:
                    job_elements = soup.find_all('article', class_='job')
                    
                if not job_elements:
                    job_elements = soup.find_all('div', class_='position')
                
                if not job_elements:
                    job_elements = soup.find_all('div', class_='job-card')
                
                # Generic fallback
                if not job_elements:
                    job_elements = soup.find_all('div', attrs={'data-position': True})
                
                for job_element in job_elements[:5]:  # Limit per keyword
                    try:
                        # Extract job information with multiple selectors
                        title_elem = (
                            job_element.find('h2', class_='position-title') or
                            job_element.find('h3', class_='job-title') or
                            job_element.find('a', class_='job-link') or
                            job_element.find('h2') or
                            job_element.find('h3')
                        )
                        
                        # Find company
                        company_elem = (
                            job_element.find('span', class_='company') or
                            job_element.find('div', class_='company-name') or
                            job_element.find('p', class_='company') or
                            job_element.find('span', class_='client')
                        )
                        
                        # Find location
                        location_elem = (
                            job_element.find('span', class_='location') or
                            job_element.find('div', class_='job-location') or
                            job_element.find('span', class_='place')
                        )
                        
                        # Find salary if available
                        salary_elem = (
                            job_element.find('span', class_='salary') or
                            job_element.find('div', class_='compensation')
                        )
                        
                        # Find link
                        link_elem = (
                            job_element.find('a', href=True) or
                            title_elem.find_parent('a') if title_elem else None
                        )
                        
                        if title_elem and link_elem:
                            title = title_elem.get_text(strip=True)
                            company = company_elem.get_text(strip=True) if company_elem else 'Epunkt Client'
                            location = location_elem.get_text(strip=True) if location_elem else 'Austria'
                            
                            # Add salary info if available
                            if salary_elem:
                                salary = salary_elem.get_text(strip=True)
                                if salary:
                                    title += f" ({salary})"
                            
                            # Construct full URL
                            job_url = link_elem.get('href')
                            if job_url.startswith('/'):
                                job_url = "https://www.epunkt.com" + job_url
                            elif not job_url.startswith('http'):
                                job_url = "https://www.epunkt.com/" + job_url
                            
                            # Clean up extracted text
                            title = title.replace('\n', ' ').replace('\t', ' ').strip()
                            company = company.replace('\n', ' ').replace('\t', ' ').strip()
                            location = location.replace('\n', ' ').replace('\t', ' ').strip()
                            
                            # Check if job should be excluded
                            if should_exclude_job(title):
                                continue
                            
                            # Check if job is already in database
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
                                
                                logger.info(f"Found job: {title} at {company}")
                        
                    except Exception as e:
                        logger.error(f"Error parsing epunkt.com job element: {e}")
                        continue
            
            else:
                logger.warning(f"epunkt.com returned status code: {response.status_code}")
            
            # Rate limiting
            time.sleep(random.uniform(3, 5))
            
        except Exception as e:
            logger.error(f"Error checking epunkt.com for {keyword}: {e}")
            continue
    
    logger.info(f"✅ Found {len(new_jobs)} new jobs on epunkt.com")
    return new_jobs


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
                
                # Indeed changes their selectors frequently
                job_elements = (soup.find_all('div', class_='job_seen_beacon') or 
                              soup.find_all('a', attrs={'data-jk': True}) or
                              soup.find_all('div', class_='jobsearch-SerpJobCard'))
                
                for job_element in job_elements[:3]:  # Limit per keyword
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

# ==================== MAIN EXECUTION ====================
def main_job_check():
    """Main function to check all job sources"""
    start_time = datetime.now()
    logger.info("🚀 Starting job check cycle...")
    
    all_new_jobs = []
    
    # Check each source and send individual notifications
    sources = [
        ("jobs.at", check_jobs_at),
        ("karriere.at", check_karriere_at),
        ("stepstone.at", check_stepstone_at),
        ("indeed.at", check_indeed_at)
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
                    time.sleep(2)  # Small delay between messages
            
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
        
        # Source breakdown
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
    test_message += "• jobs.at\n• karriere.at\n• stepstone.at\n• indeed.at\n\n"
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
    
    conn.close()
    
    summary = f"📊 <b>Daily Job Search Summary</b>\n\n"
    summary += f"📈 Today's new jobs: {today_count}\n"
    summary += f"🎯 Total jobs tracked: {total_count}\n\n"
    
    if source_breakdown:
        summary += "📋 Today's breakdown:\n"
        for source, count in source_breakdown:
            summary += f"• {source}: {count} jobs\n"
    
    summary += "\n🔍 Keep applying and stay motivated! 💪"
    
    send_telegram_message(summary)

# ==================== SCHEDULING ====================
def start_scheduler():
    """Start the job scheduler"""
    logger.info("Starting job scheduler...")
    
    # Initialize database
    init_database()
    
    # Send test message
    send_test_message()
    
    # Wait 30 seconds then run first check
    time.sleep(30)
    
    # Schedule job checks every 2 hours
    schedule.every(2).hours.do(main_job_check)
    
    # Schedule a daily summary at 9 AM
    schedule.every().day.at("09:00").do(send_daily_summary)
    
    # Run initial check
    main_job_check()
    
    logger.info("Scheduler started. Running continuously...")
    
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute

if __name__ == "__main__":
    print("🚀 Starting Austrian Job Scraper Bot...")
    print("📱 Telegram notifications enabled")
    print("🔍 Monitoring job portals every 2 hours")
    print("⏹️  Press Ctrl+C to stop")
    
    try:
        start_scheduler()
    except KeyboardInterrupt:
        print("\n🛑 Job scraper stopped by user")
        logger.info("Job scraper stopped by user")
    except Exception as e:
        print(f"❌ Error: {e}")
        logger.error(f"Fatal error: {e}")