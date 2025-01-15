import os
import sys
import time
import threading
import subprocess
from pathlib import Path
from getpass import getpass
from pydantic import BaseModel
from dotenv import dotenv_values
import yaml
import nest_asyncio
from crewai import Agent, Task, Crew
from crewai_tools import DirectoryReadTool, FileReadTool

# Apply a patch to allow nested asyncio loops in Jupyter
nest_asyncio.apply()


# --------------------------- Helper Classes and Functions ---------------------------

class LoadingAnimation:
    def __init__(self):
        self.stop_event = threading.Event()
        self.animation_thread = None

    def _animate(self, message="Loading"):
        chars = "/\\|"
        while not self.stop_event.is_set():
            for char in chars:
                sys.stdout.write(f'\r{message}... {char}')
                sys.stdout.flush()
                time.sleep(0.1)
                if self.stop_event.is_set():
                    sys.stdout.write("\n")
                    break

    def start(self, message="Loading"):
        self.stop_event.clear()
        self.animation_thread = threading.Thread(target=self._animate, args=(message,))
        self.animation_thread.daemon = True
        self.animation_thread.start()

    def stop(self, completion_message="Complete"):
        self.stop_event.set()
        if self.animation_thread:
            self.animation_thread.join()
        print(f"\r{completion_message} ✓")


# --------------------------- Environment Setup ---------------------------


# Load .env file if it exists
def load_dotenv(dotenv_path='.env'):
    env_vars = dotenv_values(dotenv_path)
    for key, value in env_vars.items():
        if key and value:
            os.environ[key] = value


load_dotenv()

# Load environment variables
if not os.environ.get("NVIDIA_NIM_API_KEY", "").startswith("nvapi-"):
    nvapi_key = getpass("Enter your NVIDIA API key: ")
    assert nvapi_key.startswith("nvapi-"), f"{nvapi_key[:5]}... is not a valid key"
    os.environ["NVIDIA_NIM_API_KEY"] = nvapi_key


# --------------------------- Classes for Documentation Flow ---------------------------

class DocItem(BaseModel):
    title: str
    description: str
    prerequisites: str
    examples: list[str]
    goal: str


class DocPlan(BaseModel):
    overview: str
    docs: list[DocItem]


class DocumentationState(BaseModel):
    project_url: str
    repo_path: Path
    docs: list[str] = []


# --------------------------- Flow Implementation ---------------------------

class CreateDocumentationFlow:
    def __init__(self, state: DocumentationState):
        self.state = state

    def clone_repo(self):
        print(f"# Cloning repository: {self.state.project_url}\n")
        repo_name = self.state.project_url.split("/")[-1].replace(".git", "")
        self.state.repo_path = Path(f"workdir/{repo_name}")

        if self.state.repo_path.exists():
            print(f"# Repository already exists at {self.state.repo_path}, removing it...\n")
            subprocess.run(["rm", "-rf", str(self.state.repo_path)])

        subprocess.run(["git", "clone", self.state.project_url, str(self.state.repo_path)])

    def plan_docs(self):
        print(f"# Planning documentation for: {self.state.repo_path}\n")
        # Use configuration files from the current project directory
        config_dir = Path("config")
        planner_config = config_dir / "planner_agents.yaml"
        tasks_config = config_dir / "planner_tasks.yaml"

        if not planner_config.exists() or not tasks_config.exists():
            raise FileNotFoundError("Planner configuration files are missing in the local project directory.")

        with open(planner_config, 'r') as f:
            agents_config = yaml.safe_load(f)

        with open(tasks_config, 'r') as f:
            tasks_config = yaml.safe_load(f)

        code_explorer = Agent(
            config=agents_config['code_explorer'],
            tools=[DirectoryReadTool(), FileReadTool()]
        )
        documentation_planner = Agent(
            config=agents_config['documentation_planner'],
            tools=[DirectoryReadTool(), FileReadTool()]
        )

        analyze_codebase = Task(config=tasks_config['analyze_codebase'], agent=code_explorer)
        create_documentation_plan = Task(config=tasks_config['create_documentation_plan'], agent=documentation_planner,
                                         output_pydantic=DocPlan)

        planning_crew = Crew(
            agents=[code_explorer, documentation_planner],
            tasks=[analyze_codebase, create_documentation_plan],
            verbose=False
        )

        result = planning_crew.kickoff(inputs={'repo_path': str(self.state.repo_path)})
        return result.pydantic

    def create_docs(self, plan: DocPlan):
        print(f"# Creating documentation\n")
        config_dir = Path("config")

        with open(config_dir / "documentation_agents.yaml", 'r') as f:
            agents_config = yaml.safe_load(f)

        with open(config_dir / "documentation_tasks.yaml", 'r') as f:
            tasks_config = yaml.safe_load(f)

        overview_writer = Agent(
            config=agents_config['overview_writer'],
            tools=[DirectoryReadTool(), FileReadTool()]
        )
        documentation_reviewer = Agent(
            config=agents_config['documentation_reviewer'],
            tools=[DirectoryReadTool(), FileReadTool()]
        )

        draft_documentation = Task(
            config=tasks_config['draft_documentation'],
            agent=overview_writer
        )
        qa_review_documentation = Task(
            config=tasks_config['qa_review_documentation'],
            agent=documentation_reviewer
        )

        documentation_crew = Crew(
            agents=[overview_writer, documentation_reviewer],
            tasks=[draft_documentation, qa_review_documentation],
            verbose=False
        )

        docs_dir = self.state.repo_path / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)

        for doc in plan.docs:
            result = documentation_crew.kickoff(inputs={
                'repo_path': str(self.state.repo_path),
                'title': doc.title,
                'overview': plan.overview,
                'description': doc.description,
                'prerequisites': doc.prerequisites,
                'examples': "\n".join(doc.examples),
                'goal': doc.goal
            })

            doc_path = docs_dir / (doc.title.lower().replace(" ", "_") + ".mdx")
            with open(doc_path, "w") as doc_file:
                doc_file.write(result.raw)

            self.state.docs.append(str(doc_path))

    def run(self):
        loader = LoadingAnimation()

        loader.start("Cloning repository")
        self.clone_repo()
        loader.stop("Repository cloned")

        loader.start("Planning documentation")
        plan = self.plan_docs()
        loader.stop("Documentation planned")

        loader.start("Creating documentation")
        self.create_docs(plan)
        loader.stop("Documentation created")


# --------------------------- Main Script ---------------------------

def main():
    project_url = input("Enter the GitHub repository URL: ").strip()
    state = DocumentationState(project_url=project_url, repo_path=Path("workdir/"))
    flow = CreateDocumentationFlow(state)

    flow.run()
    print("\n# Documentation flow completed.")


if __name__ == "__main__":
    main()
