async function uploadFile() {
    const fileInput = document.getElementById("fileInput");
    const statusDiv = document.getElementById("status");
    const outputDiv = document.getElementById("output");

    const file = fileInput.files[0];

    if (!file) {
        alert("Please select a file");
        return;
    }

    const formData = new FormData();
    formData.append("file", file);

    statusDiv.innerText = "Processing...";
    outputDiv.innerText = "";

    try {
        const response = await fetch("http://localhost:8000/api/upload", {
            method: "POST",
            body: formData
        });

        if (!response.ok) {
            throw new Error("Failed to process file");
        }

        const data = await response.json();

        statusDiv.innerText = "Completed ✅";

        outputDiv.innerText = JSON.stringify(data, null, 2);

    } catch (error) {
        statusDiv.innerText = "Error ❌";
        outputDiv.innerText = error.message;
    }
}