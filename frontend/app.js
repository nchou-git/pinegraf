async function runEnrich() {
  const answer = document.getElementById("answer");
  answer.textContent = "Running enrichment...";

  const response = await fetch("/enrich", { method: "POST" });
  const data = await response.json();
  answer.textContent = `Enriched ${data.enriched_count} alumni records.`;
}

async function runQuery() {
  const input = document.getElementById("question");
  const answer = document.getElementById("answer");
  const question = input.value.trim();

  if (!question) {
    answer.textContent = "Please enter a question.";
    return;
  }

  answer.textContent = "Querying...";
  const response = await fetch("/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  const data = await response.json();
  answer.textContent = data.answer;
}

document.getElementById("enrichBtn").addEventListener("click", runEnrich);
document.getElementById("queryBtn").addEventListener("click", runQuery);
