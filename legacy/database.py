import sqlite3

#create a database connection
def create_connection(db_file):
    conn = None
    try:
        conn = sqlite3.connect(db_file)
        print("Connection to database successful")
    except sqlite3.Error as e:
        print(f"Error connecting to database: {e}")
    return conn

#create database tables
def create_tables(conn):
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS novels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            url TEXT NOT NULL UNIQUE
        )
    ''')
        