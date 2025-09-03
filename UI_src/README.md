# RAVEN User Interface for LOGOS and DACKAR

This project contains a visualization app for the LOGOS and DACKAR RAVEN apps. The interface is a web-based application with
postgreSQL backend for database management. For compability, the application utilizes a docker container that wraps the postgreSQL and Streamlit together.
 
# Quick Start

1. Clone the project directory into your computer system with all associated files
2. Download and install the Docker application. Detailed instructions below.
3. (optional) Download and install Anaconda and run the environment DACKAR_v0. Detailed instructions below.
4. Navigate to the folder directory in an Anaconda terminal with the DACKAR_v0 environment or using powershell or a command line terminal of your choice. Use ```cd``` to change directory and ```dir``` or ```ls``` to show current directory contents 
5. Run the application using the following commands:
```bash
docker-compose up --build
```
6. After the docker-compose finishes in the termical, open up a new web browser and type ```http://localhost:8501/```
7. The app should appear with a menu bar on the side. The server connection is successfully if on the side bar, a green box notification pops up.

# Requirements
- Docker
- Conda

## Info related to Docker
Docker desktop is the preferred application to run the application during development. The docker image and build files are included in this repo; however the docker application is required to run them. 

### Windows Installation
This is the original operating system that the container was developed on. Compatibility with other operating systems cannot currently be guaranteed. 

1. Go to https://docs.docker.com/desktop/setup/install/windows-install/
2. Select "Docker Desktop for Windows - x86_64"
3. Follow the steps in the Docker installation wizard.
    - After installation, the Docker application will ask if you have WSL turned on. For Docker to operate properly, you will need to turn these features on.
5. In the search bar of your windows machine, type "Windows Features". An option should appear that says "Turn Windows features on or off". Click this option.
6. Check that the following boxes are ticked: "Windows Subsystem for Linux", "Virtual Machine Platform", and "Windows Hypervisor Platform".
7. After these are ticked and OK is pressed, the necessary files will be updated/installed and a restart will be needed.
8. After restarting, to check if WSL is correctly implemented, open a powershell terminal and type ```wsl --version```
9. If WSL is missing, use the command ```wsl --install``` to install WSL
10. Check that the latest WSL is installed by running the command ```wsl --update```

## Info Related to Anaconda
Anaconda is the preferred development environment to modify the app and all dependencies

### Installation Instructions
1. The installation for Anaconda is typical with no special considerations. Follow the steps found in https://www.anaconda.com/docs/getting-started/anaconda/install#windows-installation to download and install. 
2. After installation, open an Anaconda prompt terminal. The terminal should look like this:
![conda](https://github.com/idaholab/LOGOS/blob/chenE/BaseUI/UI_src/README_assets/image.jpg?raw=true)

## Info Related to PostgreSQL (Database Management)
Installation of PostgreSQL is only necessary if you are an app developer. This is separate from the anaconda installation pathway and is **necessary** Otherwise, these steps can be bypassed. Streamlit is the python application used to develop the web app. Streamlit does not need to be installed if only the container is to be run. This is because the docker compose file contain instructions to automatically install and setup Streamlit. 
The default environment variables for Postgre are located in environment. These are used to access the database. A password is necessary and is defaulted to "Password". 

## Info Related to Streamlit
Installation of Streamlit is only necessary if you are an app developer. Furthermore, if you DID NOT use the anaconda installation pathway, you will need to install Streamlit. Otherwise, these steps can be bypassed. Streamlit is the python application used to develop the web app. Streamlit does not need to be installed if only the container is to be run. This is because the docker compose file contain instructions to automatically install and setup Streamlit. 

### Installation
