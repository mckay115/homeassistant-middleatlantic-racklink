# Home Assistant Integration for Middle Atlantic RackLink API

![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Integration-blue)
![License](https://img.shields.io/github/license/mckay115/homeassistant-middleatlantic-racklink)
![Version](https://img.shields.io/github/v/release/mckay115/homeassistant-middleatlantic-racklink?include_prereleases)

## Overview

This custom integration enables Home Assistant to interface with Middle Atlantic RackLink API-enabled devices such as Power Sequencers and UPS units. It allows you to monitor and control these devices directly from your Home Assistant setup, providing enhanced automation and management capabilities.

## Features

- Monitor the status of RackLink-enabled devices
- Control power sequencers and UPS devices
- Integration with Home Assistant automation and scripts
- Support for multiple devices

## Installation

### HACS (Home Assistant Community Store)

1. Ensure you have [HACS](https://hacs.xyz/) installed in your Home Assistant setup.
2. Open HACS in Home Assistant.
3. Go to the Integrations section.
4. Click on the three dots in the top right corner and select "Custom repositories."
5. Add the repository URL: `https://github.com/yourusername/repo` and select the category as "Integration."
6. Find the "Middle Atlantic RackLink API" integration in HACS and click "Install."
7. Restart Home Assistant.

### Manual Installation

1. Download the repository as a ZIP file.
2. Extract the contents and copy the `custom_components/racklink` directory to your Home Assistant `config/custom_components` directory.
3. Restart Home Assistant.

## Configuration

1. In Home Assistant, go to `Configuration` > `Integrations`.
2. Click on "Add Integration."
3. Search for "Middle Atlantic RackLink API" and select it.
4. Follow the prompts to set up the integration, including entering the necessary API details.

## Usage

Once configured, you can use the integration to monitor and control your RackLink-enabled devices. The devices will appear in Home Assistant as entities, allowing you to incorporate them into your automations, scripts, and dashboards.
