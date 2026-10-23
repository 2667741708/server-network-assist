import repositories from "../../../projects/catalog.json";
import documentation from "../../../projects/documentation.json";

// Curated public documentation does not belong to the exported public-repo list.
// Keeping it separate also preserves it when build_project_hub.py --sync runs.
const projects = [...documentation.projects, ...repositories.projects];
if (new Set(projects.map(project => project.name)).size !== projects.length) {
  throw new Error("Duplicate project documentation route");
}
export default { ...repositories, projects };
