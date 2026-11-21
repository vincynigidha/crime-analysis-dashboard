-- Crime Analysis Dashboard - Database Schema
-- Run:
  

CREATE DATABASE IF NOT EXISTS crime_analysis;
USE crime_analysis;

-- Module 1: Users (Admin login)
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(100),
    email VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Module 3: Crime Category
CREATE TABLE IF NOT EXISTS crime_category (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Module 6: Location (used by crime_record + Leaflet map)
CREATE TABLE IF NOT EXISTS location (
    id INT AUTO_INCREMENT PRIMARY KEY,
    area_name VARCHAR(100) NOT NULL,
    district VARCHAR(100),
    latitude DECIMAL(10, 6) NOT NULL,
    longitude DECIMAL(10, 6) NOT NULL
);

-- Module 4: Crime Record (core table, used by Modules 2, 4, 5, 6, 8)
CREATE TABLE IF NOT EXISTS crime_record (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(150) NOT NULL,
    description TEXT,
    category_id INT,
    location_id INT,
    status ENUM('Pending', 'Solved') DEFAULT 'Pending',
    reported_date DATE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES crime_category(id) ON DELETE SET NULL,
    FOREIGN KEY (location_id) REFERENCES location(id) ON DELETE SET NULL
);

-- Seed: one admin user -> username: admin / password: admin123
-- (hash generated with werkzeug.security.generate_password_hash, see create_admin.py)
