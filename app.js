/* Shared browser logic for the Smart Library System. */
(function () {
  "use strict";

  const API = "/api";
  const INSTITUTIONAL_EMAIL_DOMAIN = "@paterostechnologicalcollege.edu.ph";
  const INSTITUTIONAL_EMAIL_PATTERN = /^[A-Za-z0-9._%+-]+@paterostechnologicalcollege\.edu\.ph$/i;
  let currentUser = null;
  let books = [];
  let transactions = [];
  let managedUsers = [];
  let activeBook = null;
  let addingBook = false;
  let coverPreviewUrl = null;

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (character) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character];
    });
  }

  async function request(path, options) {
    const settings = options || {};
    settings.headers = Object.assign({}, settings.headers || {});
    if (settings.body instanceof FormData) {
      delete settings.headers["Content-Type"];
    } else if (!settings.headers["Content-Type"]) {
      settings.headers["Content-Type"] = "application/json";
    }
    const response = await fetch(API + path, settings);
    const result = await response.json().catch(function () { return {}; });
    if (!response.ok) {
      if (response.status === 401 && window.location.pathname.indexOf(".html") !== -1 &&
          !["/LogIn.html", "/welcome.html", "/EmailVerification.html", "/VerificationCode.html"].includes(window.location.pathname)) {
        window.location.href = "/LogIn.html";
      }
      throw new Error(result.error || "The request could not be completed.");
    }
    return result;
  }

  function notify(message, isError) {
    let notice = document.querySelector(".api-notice");
    if (!notice) {
      const style = document.createElement("style");
      style.textContent = ".api-notice{position:fixed;z-index:200;top:16px;right:16px;max-width:min(420px,calc(100vw - 32px));padding:12px 16px;background:#e2f4e7;color:#174b2e;border:1px solid #39744e;border-radius:6px;box-shadow:0 3px 12px #0002;font:600 14px/1.4 Arial,sans-serif}.api-notice.error{background:#fff0ed;color:#812a20;border-color:#b85649}";
      document.head.appendChild(style);
      notice = document.createElement("div");
      notice.className = "api-notice";
      notice.setAttribute("role", "status");
      document.body.appendChild(notice);
    }
    notice.textContent = message;
    notice.classList.toggle("error", Boolean(isError));
    clearTimeout(notice.timer);
    notice.timer = setTimeout(function () { notice.remove(); }, 4200);
  }

  function addInteractiveStyles() {
    const style = document.createElement("style");
    style.textContent = ".book-actions{display:flex;gap:4px;flex-wrap:wrap}.book-actions button{border:1px solid #224332;background:#e7f1e8;color:#1e3f2e;border-radius:4px;padding:5px 7px;font:600 11px Arial,sans-serif;cursor:pointer}.book-actions button:hover{background:#d5e8d8}.filter-grid .filter-field input{box-sizing:border-box;width:100%;min-width:0;border:0;outline:0;background:transparent;font:600 13px Arial,sans-serif;color:#1e3f2e}.api-dialog{width:min(480px,calc(100vw - 32px));max-height:calc(100vh - 32px);padding:0;border:2px solid #224332;border-radius:6px;color:#1e3f2e;box-shadow:0 12px 36px #0004}.api-dialog::backdrop{background:#10201888}.api-dialog form{padding:22px}.api-dialog h2{margin:0 0 16px;font:700 20px Arial,sans-serif}.api-dialog p{font:14px/1.5 Arial,sans-serif}.api-dialog .dialog-field{display:grid;gap:5px;margin:12px 0}.api-dialog label{font:600 13px Arial,sans-serif}.api-dialog input,.api-dialog select{width:100%;min-height:38px;padding:8px;border:1px solid #809487;border-radius:4px;font:14px Arial,sans-serif}.api-dialog-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}.api-dialog button{min-height:36px;padding:7px 14px;border:1px solid #224332;border-radius:4px;background:#fff;color:#1e3f2e;font:600 13px Arial,sans-serif;cursor:pointer}.api-dialog button[type=submit]{background:#224332;color:#fff}.api-dialog button:hover{filter:brightness(.96)}";
    style.textContent += ".dashboard-card-link{cursor:pointer}.dashboard-card-link:focus-visible{outline:3px solid #39744e;outline-offset:2px}.attendance-dialog{width:min(1100px,calc(100vw - 28px))}.attendance-dialog h2{display:flex;justify-content:space-between;align-items:center}.attendance-dialog h2 button{min-width:34px;padding:2px 8px;font-size:22px}.dialog-table-wrap{max-height:65vh;overflow:auto}.attendance-dialog table{width:100%;min-width:680px;border-collapse:collapse}.attendance-dialog th,.attendance-dialog td{padding:8px 10px;border-bottom:1px solid #d8e2da;text-align:left;font:12px/1.4 Arial,sans-serif}.attendance-dialog th{position:sticky;top:0;background:#224332;color:#fff}.attendance-dialog td{color:#1e3f2e}#log-rows td{white-space:nowrap}";
    document.head.appendChild(style);
    const coverStyle = document.createElement("style");
    coverStyle.textContent = ".cover-controls{display:grid;gap:7px;margin-top:12px}.cover-controls label{display:block;padding:7px 9px;background:#e7f1e8;color:#1e3f2e;border:1px solid #809487;border-radius:4px;font:600 12px Arial,sans-serif;text-align:center;cursor:pointer}.cover-controls input{width:100%;font:11px Arial,sans-serif}.cover-controls>div{display:flex;gap:6px}.cover-controls button{flex:1;padding:6px 8px;border:1px solid #224332;border-radius:4px;background:#e7f1e8;color:#1e3f2e;font:600 11px Arial,sans-serif;cursor:pointer}.cover-controls small{font:11px/1.3 Arial,sans-serif;overflow-wrap:anywhere}#bookCoverImage{max-height:300px;object-fit:contain}";
    document.head.appendChild(coverStyle);
    const managementStyle = document.createElement("style");
    managementStyle.textContent = ".user-view-tabs{display:flex;gap:6px}.user-view-tabs button,.system-role-filter{min-height:34px;padding:7px 10px;border:1px solid #224332;border-radius:4px;background:#fff;color:#1e3f2e;font:600 12px Arial,sans-serif;cursor:pointer}.user-view-tabs button.active{background:#224332;color:#fff}.system-role-filter[hidden]{display:none}.top-action-bar{align-items:center;gap:10px;flex-wrap:wrap}.people-dialog{overflow:auto}.people-dialog form{max-height:calc(100vh - 48px);overflow:auto}.filter-grid .field select,.filter-grid .field input{box-sizing:border-box;width:100%;min-height:34px;padding:6px 8px;border:1px solid #839789;border-radius:4px;background:#fff;color:#1e3f2e;font:13px Arial,sans-serif}";
    document.head.appendChild(managementStyle);
  }

  function clearExampleRows(path) {
    const page = path.split("/").pop();
    if (page === "dashboard.html") {
      document.querySelectorAll(".metrics .num").forEach(function (value) { value.textContent = "--"; });
      const body = document.getElementById("log-rows");
      if (body) body.innerHTML = '<tr><td colspan="5">Loading attendance activity...</td></tr>';
    } else if (page === "book_transactions.html") {
      const body = document.querySelector(".grid-table tbody");
      if (body) body.innerHTML = '<tr><td colspan="9">Loading catalog...</td></tr>';
    } else if (page === "reports.html") {
      const table = document.querySelector(".main table");
      if (table) {
        const filterTitle = document.querySelector(".filter-title");
        const heading = document.querySelector(".stage-header h2, .card-top h2");
        const description = document.querySelector(".stage-header p, .card-top p");
        if (filterTitle) filterTitle.textContent = "Filter Attendance Records";
        if (heading) heading.textContent = "Complete Attendance Records";
        if (description) description.textContent = "Official Time-in and Time-out history recorded by the Smart Library backend.";
        table.querySelector("thead").innerHTML = "<tr><th>SCHOOL ID</th><th>NAME</th><th>ROLE</th><th>DEPARTMENT</th><th>DATE</th><th>TIME-IN</th><th>TIME-OUT</th><th>STATUS</th></tr>";
        table.querySelector("tbody").innerHTML = '<tr><td colspan="8">Loading attendance records...</td></tr>';
      }
    } else if (page === "users_management.html") {
      const body = document.querySelector(".stage tbody");
      if (body) body.innerHTML = '<tr><td colspan="4">Loading users...</td></tr>';
    } else if (page === "time_in_out.html") {
      const body = document.querySelector(".table-container tbody, .stage tbody");
      if (body) body.innerHTML = '<tr><td colspan="8">Loading attendance records...</td></tr>';
    }
  }

  function showDialog(title, fields, message, submitLabel, onSubmit, onReady) {
    const dialog = document.createElement("dialog");
    dialog.className = "api-dialog";
    const inputMarkup = fields.map(function (field) {
      let control;
      if (field.type === "select") {
        control = '<select name="' + escapeHtml(field.name) + '"' + (field.required ? " required" : "") + ">" +
          field.options.map(function (option) {
            return '<option value="' + escapeHtml(option.value) + '"' + (option.value === field.value ? " selected" : "") + ">" + escapeHtml(option.label) + "</option>";
          }).join("") + "</select>";
      } else {
        control = '<input name="' + escapeHtml(field.name) + '" type="' + escapeHtml(field.type || "text") +
          '" value="' + escapeHtml(field.value || "") + '"' + (field.required ? " required" : "") +
          (field.placeholder ? ' placeholder="' + escapeHtml(field.placeholder) + '"' : "") +
          (field.readonly ? " readonly" : "") + ">";
      }
      return '<div class="dialog-field"><label>' + escapeHtml(field.label) + control + "</label></div>";
    }).join("");
    dialog.innerHTML = '<form method="dialog"><h2>' + escapeHtml(title) + "</h2>" +
      (message ? "<p>" + escapeHtml(message) + "</p>" : "") + inputMarkup +
      '<div class="api-dialog-actions"><button type="button" data-cancel>Cancel</button><button type="submit">' +
      escapeHtml(submitLabel || "Save") + "</button></div></form>";
    document.body.appendChild(dialog);
    dialog.addEventListener("close", function () { dialog.remove(); });
    dialog.querySelector("[data-cancel]").addEventListener("click", function () { dialog.close(); });
    dialog.querySelector("form").addEventListener("submit", function (event) {
      event.preventDefault();
      const values = Object.fromEntries(new FormData(event.currentTarget).entries());
      dialog.close();
      Promise.resolve(onSubmit(values)).catch(function (error) { notify(error.message, true); });
    });
    if (onReady) onReady(dialog);
    dialog.showModal();
  }

  function login(event) {
    event.preventDefault();
    const email = document.getElementById("email").value.trim();
    const password = document.getElementById("password").value;
    request("/auth/login", { method: "POST", body: JSON.stringify({ email: email, password: password }) })
      .then(function (result) {
        const destination = result.user.role === "MIS Admin" ? "/MIS_ADMIN/" : "/LIBRARIAN/";
        window.location.href = destination + "dashboard.html";
      })
      .catch(function (error) { notify(error.message, true); });
  }

  async function requestPasswordReset(requestKey, resetToken, newPassword, confirmPassword) {
    const result = await request("/auth/recovery/reset", {
      method: "POST",
      body: JSON.stringify({
        request_key: requestKey,
        reset_token: resetToken,
        newPassword: newPassword,
        confirmPassword: confirmPassword
      })
    });
    notify(result.message, false);
    sessionStorage.removeItem("recovery_request_key");
    sessionStorage.removeItem("recovery_reset_token");
    window.location.href = "/LogIn.html";
  }

  function togglePassword(inputId) {
    const input = document.getElementById(inputId || "password");
    if (input) input.type = input.type === "password" ? "text" : "password";
  }

  function bindLogout() {
    document.querySelectorAll(".logout").forEach(function (link) {
      link.addEventListener("click", async function (event) {
        event.preventDefault();
        try {
          await request("/auth/logout", { method: "POST" });
          window.location.href = "/LogIn.html";
        } catch (error) { notify(error.message, true); }
      });
    });
  }

  function updateAccountName() {
    document.querySelectorAll(".who-name").forEach(function (element) {
      element.textContent = currentUser.name;
    });
  }

  function renderDashboard(data) {
    const metricContainer = document.querySelector(".metrics");
    if (metricContainer) {
      const stats = data.stats;
      const cards = [
        ["BORROWED BOOKS", stats.borrowed_books, "borrowed"],
        ["OVERDUE BOOKS", stats.overdue_books, "overdue"],
        ["PEOPLE CURRENTLY INSIDE", stats.people_inside, ""]
      ];
      const metricItems = metricContainer.querySelectorAll(".metric");
      cards.forEach(function (card, index) {
        const item = metricItems[index];
        if (!item) return;
        item.classList.remove("dashboard-card-link");
        item.removeAttribute("role");
        item.removeAttribute("tabindex");
        item.removeAttribute("data-dashboard-list");
        if (card[2]) {
          item.classList.add("dashboard-card-link");
          item.setAttribute("role", "button");
          item.tabIndex = 0;
          item.dataset.dashboardList = card[2];
        }
        item.querySelector(".cap").textContent = card[0];
        item.querySelector(".num").textContent = card[1];
        metricContainer.appendChild(item);
      });
    }

    const table = document.querySelector(".logs table");
    const tbody = document.getElementById("log-rows");
    if (table && tbody) {
      const heading = document.querySelector(".logs h2");
      if (heading) heading.textContent = "Live Access Logs";
      table.querySelector("thead").innerHTML = "<tr><th>SCHOOL ID</th><th>NAME</th><th>ROLE</th><th>TIME-IN</th><th>TIME-OUT</th></tr>";
      tbody.innerHTML = data.attendance.length ? data.attendance.map(function (record) {
        return "<tr><td>" + escapeHtml(record.school_id) + "</td><td>" + escapeHtml(record.name) +
          "</td><td>" + escapeHtml(record.role + (record.department ? " / " + record.department : "")) +
          "</td><td>" + escapeHtml(record.time_in) + "</td><td>" + escapeHtml(record.time_out || "--") + "</td></tr>";
      }).join("") : '<tr><td colspan="5">No attendance records yet.</td></tr>';
    }
    bindDashboardCards();
  }

  async function bindRecoveryRequests() {
    const body = document.getElementById("recovery-request-rows");
    const pendingCount = document.getElementById("recoveryPendingCount");
    if (!body || !pendingCount) return;

    async function refresh() {
      const result = await request("/auth/recovery/requests");
      const requests = result.requests || [];
      const pending = requests.filter(function (item) { return item.status === "pending"; });
      pendingCount.textContent = pending.length + " pending";
      body.innerHTML = requests.length ? requests.map(function (item) {
        const statusLabels = {
          pending: "Pending Approval",
          approved: "Approved",
          rejected: "Rejected",
          expired: "Expired",
          completed: "Completed"
        };
        let actions = '<span class="recovery-action">—</span>';
        if (item.status === "pending") {
          actions = '<div class="recovery-action"><button type="button" data-action="view" data-request-id="' + item.id + '">View Request</button>' +
            '<button type="button" data-action="approve" data-request-id="' + item.id + '">Approve</button>' +
            '<button type="button" data-action="reject" data-request-id="' + item.id + '">Reject</button></div>';
        } else if (item.status === "completed" || item.status === "expired") {
          actions = '<div class="recovery-action"><button type="button" data-action="delete" data-request-id="' + item.id + '">Delete</button></div>';
        }
        return '<tr data-request-id="' + item.id + '">' +
          '<td>' + escapeHtml(item.librarian_name) + '</td>' +
          '<td>' + escapeHtml(item.email) + '</td>' +
          '<td>' + escapeHtml(item.requested_at) + '</td>' +
          '<td class="recovery-status">' + escapeHtml(statusLabels[item.status] || item.status) + '</td>' +
          '<td>' + actions + '</td></tr>';
      }).join("") : '<tr><td colspan="5">No recovery requests.</td></tr>';
    }

    body.addEventListener("click", function (event) {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const requestId = Number(button.dataset.requestId);
      const row = button.closest("tr");
      if (button.dataset.action === "view") {
        showDialog("Password Recovery Request", [], [
          "Librarian: " + row.cells[0].textContent,
          "Institutional email: " + row.cells[1].textContent,
          "Requested: " + row.cells[2].textContent,
          "Status: " + row.cells[3].textContent,
          "Verify the Librarian in person before approving. The code is only shown after approval."
        ].join("\n\n"), "Close", function () {});
        return;
      }
      if (button.dataset.action === "delete") {
        showDialog(
          "Delete Recovery Request",
          [],
          "Permanently delete the " + row.cells[3].textContent.toLowerCase() +
            " password recovery request for " + row.cells[0].textContent + "?",
          "Delete",
          async function () {
            const result = await request("/auth/recovery/requests/" + requestId, { method: "DELETE" });
            notify(result.message);
            await refresh();
          }
        );
        return;
      }
      const decision = button.dataset.action;
      const decisionLabel = decision === "approve" ? "Approve" : "Reject";
      showDialog(
        decisionLabel + " Recovery Request",
        [],
        decision === "approve"
          ? "Confirm that you verified " + row.cells[0].textContent + " in person. A one-time code will be displayed once for private handoff."
          : "Reject the password recovery request for " + row.cells[0].textContent + "? The request will not be usable.",
        "Confirm " + decisionLabel,
        async function () {
          const result = await request("/auth/recovery/approve", {
            method: "POST",
            body: JSON.stringify({ request_id: requestId, decision: decision })
          });
          if (result.recovery_code) {
            showDialog(
              "One-Time Recovery Code",
              [],
              "Give this code directly to the Librarian you verified in person. It expires at " +
                result.expires_at + ". Do not send or record it elsewhere.\n\nCode: " + result.recovery_code,
              "I Have Provided It",
              async function () {
                await refresh();
              }
            );
          } else {
            notify(result.message);
            await refresh();
          }
        }
      );
    });

    await refresh();
    window.setInterval(function () {
      refresh().catch(function (error) { notify(error.message, true); });
    }, 10000);
  }

  function showRowsDialog(title, headers, rows) {
    const dialog = document.createElement("dialog");
    dialog.className = "api-dialog attendance-dialog";
    const headerMarkup = headers.map(function (header) { return "<th>" + escapeHtml(header) + "</th>"; }).join("");
    const rowMarkup = rows.length ? rows.map(function (row) {
      return "<tr>" + row.map(function (cell) { return "<td>" + escapeHtml(cell) + "</td>"; }).join("") + "</tr>";
    }).join("") : '<tr><td colspan="' + headers.length + '">No records found.</td></tr>';
    dialog.innerHTML = '<form method="dialog"><h2>' + escapeHtml(title) +
      '<button type="submit" aria-label="Close">&times;</button></h2><div class="dialog-table-wrap"><table><thead><tr>' +
      headerMarkup + "</tr></thead><tbody>" + rowMarkup + '</tbody></table></div></form>';
    document.body.appendChild(dialog);
    dialog.addEventListener("close", function () { dialog.remove(); });
    dialog.showModal();
  }

  function bindDashboardCards() {
    const metricContainer = document.querySelector(".metrics");
    if (!metricContainer || metricContainer.dataset.cardsBound) return;
    metricContainer.dataset.cardsBound = "true";
    const openCard = function (card) {
      const open = async function () {
        const kind = card.dataset.dashboardList;
        const result = await request("/dashboard/" + kind + "-books");
        const records = result.books.map(function (book) {
          const row = [book.school_id, book.borrower_name, book.barcode, book.title, book.borrow_date, book.due_date];
          if (kind === "overdue") row.push(book.days_overdue + " days", book.status);
          else row.push(book.status);
          return row;
        });
        const headers = ["School ID", "Borrower", "Book ID / Barcode", "Book Title", "Borrow Date", "Due Date"];
        headers.push(kind === "overdue" ? "Days Overdue" : "Status");
        if (kind === "overdue") headers.push("Status");
        showRowsDialog(kind === "overdue" ? "Overdue Books" : "Borrowed Books", headers, records);
      };
      open().catch(function (error) { notify(error.message, true); });
    };
    metricContainer.addEventListener("click", function (event) {
      const card = event.target.closest(".dashboard-card-link");
      if (card) openCard(card);
    });
    metricContainer.addEventListener("keydown", function (event) {
      const card = event.target.closest(".dashboard-card-link");
      if (card) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openCard(card);
        }
      }
    });
  }

  function bookFields() {
    const result = {};
    const names = {
      "book number": "book_number", "barcode": "barcode", "book title": "title", "author name": "author",
      "category": "category", "date published": "published_date", "book count": "quantity", "status": "status"
    };
    document.querySelectorAll("#bookModal .form-group").forEach(function (group) {
      const label = group.querySelector("label");
      const input = group.querySelector("input");
      if (label && input && names[label.textContent.trim().toLowerCase()]) {
        result[names[label.textContent.trim().toLowerCase()]] = input;
      }
    });
    return result;
  }

  function setBookModal(book) {
    const modal = document.getElementById("bookModal");
    if (!modal) return;
    const fields = bookFields();
    activeBook = book;
    addingBook = !book;
    if (coverPreviewUrl) {
      URL.revokeObjectURL(coverPreviewUrl);
      coverPreviewUrl = null;
    }
    const coverImage = document.getElementById("bookCoverImage");
    const coverInput = document.getElementById("bookCoverInput");
    const coverControls = document.getElementById("coverControls");
    const coverFileName = document.getElementById("coverFileName");
    if (coverImage) {
      coverImage.alt = book ? book.title + " cover" : "Book cover image";
      coverImage.src = book && book.cover_image ? "/api/books/" + book.id + "/cover" : "../img/ptc_logo.png";
      coverImage.onerror = function () { coverImage.src = "../img/ptc_logo.png"; };
    }
    if (coverInput) coverInput.value = "";
    if (coverControls) coverControls.hidden = addingBook;
    if (coverFileName) coverFileName.textContent = "Choose an image file";
    Object.keys(fields).forEach(function (key) {
      fields[key].value = book ? (book[key] == null ? "" : book[key]) : "";
      fields[key].readOnly = key === "status" || (!addingBook && key !== "status");
    });
    updateBarcodePreview(book ? book.barcode : "");
    const activeTransactions = book ? transactions.filter(function (item) {
      return item.book_id === book.id && item.status === "borrowed";
    }) : [];
    const borrower = activeTransactions[0];
    const borrowerValues = [borrower ? borrower.borrower_name : "", borrower ? borrower.borrower_school_id : "",
      borrower ? borrower.borrower_department : "", borrower ? borrower.borrower_email : ""];
    const topInputs = Array.from(modal.querySelectorAll(".form-section .form-group input")).slice(0, 4);
    topInputs.forEach(function (input, index) { input.value = borrowerValues[index]; });
    const sectionTitle = modal.querySelector(".section-title");
    if (sectionTitle) sectionTitle.lastChild.textContent = addingBook ? " ADD BOOK" : " BOOK INFORMATION";
    const next = modal.querySelector(".next-btn");
    const edit = modal.querySelector(".btn-edit");
    if (next) next.textContent = "BORROW";
    if (edit) edit.textContent = addingBook ? "CANCEL" : "EDIT";
    modal.classList.add("active");
  }

  function renderBooks() {
    const table = document.querySelector(".grid-table");
    if (!table) return;
    const categoryFilter = document.getElementById("bookCategoryFilter");
    const availabilityFilter = document.getElementById("bookAvailabilityFilter");
    const selectedCategory = categoryFilter ? categoryFilter.value.toLowerCase() : "";
    const selectedAvailability = availabilityFilter ? availabilityFilter.value : "";
    const visibleBooks = books.filter(function (book) {
      const available = Number(book.available_quantity) > 0;
      return (!selectedCategory || String(book.category || "").toLowerCase() === selectedCategory) &&
        (!selectedAvailability || (selectedAvailability === "available" ? available : !available));
    });
    const countLabel = document.getElementById("book-count-label");
    if (countLabel) countLabel.textContent = String(visibleBooks.length);
    const tbody = table.querySelector("tbody");
    tbody.innerHTML = visibleBooks.length ? visibleBooks.map(function (book) {
      const borrowed = transactions.filter(function (item) { return item.book_id === book.id && item.status === "borrowed"; });
      const borrowerNames = borrowed.map(function (item) { return item.borrower_name; }).join(", ");
      const available = Number(book.available_quantity) > 0;
      return '<tr data-book-id="' + book.id + '">' +
        '<td class="code"><strong>' + escapeHtml(book.book_number) + '</strong>' +
          '<small><span class="barcode-label">Barcode:</span><span class="barcode-value">' +
          escapeHtml(book.barcode) + '</span></small></td>' +
        '<td>' + escapeHtml(book.category) + '</td>' +
        '<td class="book-title-cell">' + escapeHtml(book.title) + '</td>' +
        '<td>' + escapeHtml(book.author) + '</td>' +
        '<td class="center">' + escapeHtml(book.published_date) + '</td>' +
        '<td class="center">' + book.quantity + '</td>' +
        '<td class="center">' + book.available_quantity + '</td>' +
        '<td><span class="status-badge ' + (available ? "available" : "unavailable") + '">' +
          (available ? "Available" : "Unavailable") + '</span>' +
          (borrowerNames ? '<small class="borrower-note">Borrowed by ' + escapeHtml(borrowerNames) + '</small>' : '') + '</td>' +
        '<td class="book-actions"><button class="action-button borrow-action" type="button" data-action="borrow" data-book-id="' + book.id +
          '"' + (available ? "" : " disabled") + '>Borrow</button>' +
        (borrowed.length ? '<button class="action-button return-action" type="button" data-action="return" data-book-id="' + book.id + '">Return</button>' : '') +
        '<button class="action-button" type="button" data-action="edit" data-book-id="' + book.id + '">Edit</button>' +
        '<button class="action-button remove-action" type="button" data-action="delete" data-book-id="' + book.id + '">Remove</button></td></tr>';
    }).join("") : '<tr><td class="empty-catalog" colspan="9">' +
      (books.length ? "No books match these filters." : "No books found. Try a different search or add a book.") + '</td></tr>';
  }

  function updateBookCategoryFilter() {
    const filter = document.getElementById("bookCategoryFilter");
    if (!filter) return;
    const selected = filter.value;
    const categories = Array.from(new Set(books.map(function (book) {
      return String(book.category || "").trim();
    }).filter(Boolean))).sort(function (left, right) { return left.localeCompare(right); });
    filter.replaceChildren(new Option("All Categories", ""));
    categories.forEach(function (category) { filter.add(new Option(category, category)); });
    if (selected && !categories.includes(selected)) filter.add(new Option(selected, selected));
    filter.value = selected;
  }

  function showBookImportResult(result) {
    const dialog = document.createElement("dialog");
    dialog.className = "api-dialog people-dialog import-result-dialog";
    const errors = (result.errors || []).map(function (item) {
      return "<li>Row " + escapeHtml(String(item.row)) +
        (item.barcode ? " (" + escapeHtml(item.barcode) + ")" : "") +
        ": " + escapeHtml(item.reason) + "</li>";
    }).join("");
    dialog.innerHTML = '<form method="dialog"><h2>Excel Import Complete</h2>' +
      '<ul class="import-summary">' +
      "<li><strong>" + result.added + "</strong> added</li>" +
      "<li><strong>" + result.skipped + "</strong> skipped</li>" +
      "<li><strong>" + (result.added + result.skipped) + "</strong> rows processed</li>" +
      "<li><strong>" + (result.matched_columns || []).length + "</strong> columns matched</li></ul>" +
      (result.matched_columns && result.matched_columns.length
        ? '<h3 class="import-columns-title">Columns matched automatically' + (result.sheet ? " (sheet: " + escapeHtml(result.sheet) + ")" : "") + '</h3><ul class="import-columns">' + result.matched_columns.map(function (item) {
          return "<li><strong>" + escapeHtml(item.field) + "</strong> &larr; " + escapeHtml(item.column) + "</li>";
        }).join("") + "</ul>" : "") +
      (result.ignored_columns && result.ignored_columns.length
        ? '<p class="import-ignored">Ignored columns: ' + escapeHtml(result.ignored_columns.join(", ")) + "</p>" : "") +
      "<p>Imported books are added to the catalog with all copies available. Borrowing transactions are not imported.</p>" +
      (errors ? '<h3 class="import-errors-title">Rows that were skipped</h3><ul class="import-errors">' + errors +
        (result.more_errors ? "<li>...and " + result.more_errors + " more.</li>" : "") + "</ul>" : "") +
      '<div class="api-dialog-actions"><button type="submit">Close</button></div></form>';
    document.body.appendChild(dialog);
    dialog.addEventListener("close", function () { dialog.remove(); });
    dialog.showModal();
  }

  async function refreshBooks(search) {
    const results = await Promise.all([
      request("/books?q=" + encodeURIComponent(search || "")),
      request("/transactions")
    ]);
    books = results[0].books;
    transactions = results[1].transactions;
    updateBookCategoryFilter();
    renderBooks();
  }

  async function borrowBook(book) {
    const dueDate = new Date();
    dueDate.setDate(dueDate.getDate() + 14);
    showDialog("Borrow Book", [
      { name: "borrower_school_id", label: "School ID", required: true, value: "" },
      { name: "institutional_email", label: "Institutional Email", type: "email", required: true, value: "", placeholder: "yourname@paterostechnologicalcollege.edu.ph" },
      { name: "borrower_name", label: "Borrower's Name", readonly: true, value: "" },
      { name: "borrower_department", label: "Course / Program", readonly: true, value: "" },
      { name: "book_number", label: "Book Number", readonly: true, value: book.book_number },
      { name: "book_title", label: "Book Title", readonly: true, value: book.title },
      { name: "barcode", label: "Barcode (scan or enter)", required: true, value: book.barcode },
      { name: "availability", label: "Availability", readonly: true, value: book.status },
      { name: "borrow_date", label: "Borrow Date", readonly: true, value: new Date().toISOString().slice(0, 10) },
      { name: "due_date", label: "Due Date", type: "date", value: dueDate.toISOString().slice(0, 10), required: true }
    ], book.title, "Confirm Borrow", async function (values) {
      const typedEmail = values.institutional_email.trim();
      if (!INSTITUTIONAL_EMAIL_PATTERN.test(typedEmail)) {
        throw new Error("The institutional email must end with " + INSTITUTIONAL_EMAIL_DOMAIN + ".");
      }
      const matches = await request("/borrowers?exact=1&q=" + encodeURIComponent(values.borrower_school_id.trim()));
      const borrower = matches.borrowers.find(function (item) {
        return item.school_id.toLowerCase() === values.borrower_school_id.trim().toLowerCase();
      });
      if (!borrower) throw new Error("No active student or faculty member has that school ID.");
      await request("/transactions/borrow", {
        method: "POST",
        body: JSON.stringify({
          borrower_school_id: borrower.school_id,
          institutional_email: typedEmail,
          barcode: values.barcode.trim(),
          due_date: values.due_date
        })
      });
      notify("Book borrowed for " + borrower.name + ".");
      await refreshBooks(document.querySelector(".search-box input")?.value || "");
    }, function (dialog) {
      const form = dialog.querySelector("form");
      const context = dialog.querySelector("p");
      const schoolId = form.elements.borrower_school_id;
      const emailInput = form.elements.institutional_email;
      const nameInput = form.elements.borrower_name;
      const programInput = form.elements.borrower_department;
      const barcodeInput = form.elements.barcode;

      function addStatusLine(input) {
        const line = document.createElement("small");
        line.setAttribute("aria-live", "polite");
        line.style.cssText = "display:block;margin-top:4px;font-size:12px;min-height:16px;";
        input.closest(".dialog-field").appendChild(line);
        return function (message, kind) {
          line.textContent = message || "";
          line.style.color = kind === "error" ? "#B3261E" : kind === "ok" ? "#1E7A3E" : "#5B7466";
        };
      }
      const setIdStatus = addStatusLine(schoolId);
      const setEmailStatus = addStatusLine(emailInput);

      function clearBorrower() {
        nameInput.value = "";
        programInput.value = "";
        if (context) context.textContent = "";
      }

      // ---- Institutional Email: typed by the librarian / MIS staff, never auto-filled ----
      function checkEmail() {
        const value = emailInput.value.trim();
        if (!value) {
          emailInput.setCustomValidity("");
          setEmailStatus("");
        } else if (!INSTITUTIONAL_EMAIL_PATTERN.test(value)) {
          const message = "Email must end with " + INSTITUTIONAL_EMAIL_DOMAIN;
          emailInput.setCustomValidity(message);
          setEmailStatus(message, "error");
        } else {
          emailInput.setCustomValidity("");
          setEmailStatus("Valid institutional email.", "ok");
        }
      }
      emailInput.addEventListener("input", checkEmail);
      emailInput.addEventListener("blur", checkEmail);

      // ---- School ID: looks up the saved record and fills Name + Course / Program ----
      let lookupTimer = null;
      let lookupSeq = 0;

      async function loadBorrower() {
        const idValue = schoolId.value.trim();
        const seq = ++lookupSeq;
        if (!idValue) {
          clearBorrower();
          setIdStatus("");
          return;
        }
        setIdStatus("Looking up borrower...", "info");
        let matches;
        try {
          matches = await request("/borrowers?exact=1&q=" + encodeURIComponent(idValue));
        } catch (error) {
          if (seq === lookupSeq) { clearBorrower(); setIdStatus(error.message, "error"); }
          return;
        }
        if (seq !== lookupSeq) return; // a newer lookup is already running

        const borrower = matches.borrowers.find(function (item) {
          return item.school_id.toLowerCase() === idValue.toLowerCase();
        });
        if (!borrower) {
          clearBorrower();
          setIdStatus("No active student or faculty member has that school ID.", "error");
          return;
        }
        nameInput.value = borrower.name;
        programInput.value = borrower.department || "";
        if (context) context.textContent = "Borrower: " + borrower.name + " · " + (borrower.department || "");
        setIdStatus("Borrower found: " + borrower.name + " (" + borrower.role + ")", "ok");
      }

      function scheduleLookup(delay) {
        clearTimeout(lookupTimer);
        lookupTimer = setTimeout(function () { loadBorrower(); }, delay);
      }

      schoolId.addEventListener("input", function () { scheduleLookup(400); });
      schoolId.addEventListener("change", function () { scheduleLookup(0); });
      schoolId.addEventListener("blur", function () { scheduleLookup(0); });
      schoolId.addEventListener("keydown", function (event) {
        if (event.key !== "Enter") return;
        event.preventDefault();
        scheduleLookup(0);
      });
      barcodeInput.addEventListener("keydown", function (event) {
        if (event.key !== "Enter") return;
        event.preventDefault();
        request("/books/barcode?value=" + encodeURIComponent(barcodeInput.value.trim())).then(function (result) {
          form.elements.book_number.value = result.book.book_number;
          form.elements.book_title.value = result.book.title;
          form.elements.availability.value = result.book.status + " (" + result.book.available_quantity + " available)";
          if (context) context.textContent = "Book: " + result.book.title + " · " + result.book.author +
            " · " + result.book.book_number + " · " + result.book.status;
          if (result.book.available_quantity < 1) notify("There are no available copies of this book.", true);
        }).catch(function (error) { notify(error.message, true); });
      });
    });
  }

  function updateBarcodePreview(value) {
    const image = document.getElementById("bookBarcodeImage");
    const printButton = document.getElementById("printBarcodeButton");
    if (!image || !printButton) return;
    if (!value) {
      image.hidden = true;
      printButton.hidden = true;
      image.removeAttribute("src");
      return;
    }
    image.src = API + "/books/barcode-image?value=" + encodeURIComponent(value);
    image.alt = "Barcode " + value;
    image.hidden = false;
    printButton.hidden = false;
  }

  async function verifyScannedBarcode(input) {
    const value = input.value.trim();
    if (!value) return;
    try {
      const result = await request("/books/barcode?value=" + encodeURIComponent(value));
      if (addingBook || (activeBook && result.book.id !== activeBook.id)) {
        throw new Error("This barcode is already assigned to another book.");
      }
      updateBarcodePreview(value);
      notify("Barcode scanned and verified.");
    } catch (error) {
      if (error.message === "Book not found.") {
        updateBarcodePreview(value);
        notify("Barcode scanned. It is available to assign.");
      } else {
        notify(error.message, true);
      }
    }
  }

  function printBarcode() {
    const image = document.getElementById("bookBarcodeImage");
    if (!image || image.hidden) return;
    const printWindow = window.open("", "_blank", "width=420,height=260");
    if (!printWindow) return notify("Allow pop-ups to print the barcode.", true);
    printWindow.document.write("<!doctype html><html><head><title>Book Barcode</title>" +
      "<style>body{font:14px Arial,sans-serif;text-align:center;padding:24px}img{max-width:100%;height:auto}</style>" +
      "</head><body><img src=\"" + escapeHtml(image.src) + "\" alt=\"" + escapeHtml(image.alt) + "\">" +
      "<script>window.onload=function(){window.print()}<\/script></body></html>");
    printWindow.document.close();
  }

  async function returnBook(book) {
    const borrowed = transactions.filter(function (item) { return item.book_id === book.id && item.status === "borrowed"; });
    if (!borrowed.length) throw new Error("There are no borrowed copies to return.");
    const choices = borrowed.map(function (item) {
      return { value: String(item.id), label: item.borrower_name + " (" + item.borrower_school_id + ")" };
    });
    showDialog("Return Book", [
      { name: "transaction_id", label: "Borrower", type: "select", options: choices, value: choices[0].value, required: true }
    ], book.title, "Confirm Return", async function (values) {
      await request("/transactions/" + Number(values.transaction_id) + "/return", { method: "POST" });
      notify("Book return recorded.");
      await refreshBooks(document.querySelector(".search-box input")?.value || "");
    });
  }

  function bindBooksPage() {
    const table = document.querySelector(".grid-table");
    if (!table) return;
    const search = document.querySelector(".search-box input");
    if (search) search.addEventListener("input", function () {
      refreshBooks(search.value).catch(function (error) { notify(error.message, true); });
    });
    const scanSearchButton = document.getElementById("scanBookButton");
    if (scanSearchButton && search) scanSearchButton.addEventListener("click", function () {
      search.focus();
      notify("Scan a book barcode with the USB scanner, then press Enter.");
    });
    if (search) search.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" || !search.value.trim()) return;
      event.preventDefault();
      request("/books/barcode?value=" + encodeURIComponent(search.value.trim())).then(function (result) {
        setBookModal(result.book);
        notify("Book found: " + result.book.title + ".");
      }).catch(function (error) { notify(error.message, true); });
    });
    table.querySelector("tbody").addEventListener("click", async function (event) {
      const button = event.target.closest("button[data-action]");
      const row = event.target.closest("tr[data-book-id]");
      if (!row) return;
      const book = books.find(function (item) { return item.id === Number(row.dataset.bookId); });
      if (!book) return;
      try {
        if (!button) return setBookModal(book);
        if (button.dataset.action === "borrow") await borrowBook(book);
        if (button.dataset.action === "return") await returnBook(book);
        if (button.dataset.action === "edit") setBookModal(book);
        if (button.dataset.action === "delete") {
          showDialog("Permanently Delete Book", [],
            book.title + " and all its borrowing history will be permanently deleted. This cannot be undone.",
            "Delete Permanently", async function () {
            await request("/books/" + book.id, { method: "DELETE" });
            notify("Book and its transaction history were permanently deleted.");
            await refreshBooks(search ? search.value : "");
          });
        }
      } catch (error) { notify(error.message, true); }
    });
    const addButton = document.querySelector(".btn-add");
    if (addButton) addButton.addEventListener("click", function () { setBookModal(null); });
    const categoryFilter = document.getElementById("bookCategoryFilter");
    const availabilityFilter = document.getElementById("bookAvailabilityFilter");
    if (categoryFilter) categoryFilter.addEventListener("change", renderBooks);
    if (availabilityFilter) availabilityFilter.addEventListener("change", renderBooks);
    const importButton = document.getElementById("importBooksButton");
    const importFile = document.getElementById("importBooksFile");
    if (importButton && importFile) {
      importButton.addEventListener("click", function () { importFile.click(); });
      importFile.addEventListener("change", async function () {
        const file = importFile.files && importFile.files[0];
        importFile.value = "";
        if (!file) return;
        if (!/\.(xlsx|xls)$/i.test(file.name)) {
          notify("Choose an Excel file (.xlsx or .xls).", true);
          return;
        }
        const label = importButton.innerHTML;
        importButton.disabled = true;
        importButton.textContent = "IMPORTING...";
        try {
          const upload = new FormData();
          upload.append("file", file);
          const result = await request("/books/import", { method: "POST", body: upload });
          if (search) search.value = "";
          await refreshBooks("");
          showBookImportResult(result);
        } catch (error) {
          notify(error.message, true);
        } finally {
          importButton.disabled = false;
          importButton.innerHTML = label;
        }
      });
    }
    const modal = document.getElementById("bookModal");
    if (!modal) return;
    const close = modal.querySelector(".close-btn");
    if (close) close.addEventListener("click", function () { modal.classList.remove("active"); });
    modal.addEventListener("click", function (event) { if (event.target === modal) modal.classList.remove("active"); });
    const edit = modal.querySelector(".btn-edit");
    if (edit) edit.addEventListener("click", function () {
      if (addingBook) return modal.classList.remove("active");
      Object.keys(bookFields()).forEach(function (key) { if (key !== "status") bookFields()[key].readOnly = false; });
    });
    const save = modal.querySelector(".btn-save");
    if (save) save.addEventListener("click", async function () {
      const fields = bookFields();
      const payload = {};
      ["book_number", "barcode", "title", "author", "category", "published_date", "quantity"].forEach(function (key) {
        payload[key] = fields[key].value.trim();
      });
      if (!payload.book_number || !payload.barcode || !payload.title || !payload.author || !payload.category || !payload.quantity) {
        return notify("Complete the book number, barcode, title, author, category, and book count.", true);
      }
      try {
        await request(addingBook ? "/books" : "/books/" + activeBook.id, {
          method: addingBook ? "POST" : "PATCH", body: JSON.stringify(payload)
        });
        modal.classList.remove("active");
        notify(addingBook ? "Book added." : "Book details updated.");
        await refreshBooks(document.querySelector(".search-box input")?.value || "");
      } catch (error) { notify(error.message, true); }
    });
    const borrowButton = modal.querySelector(".next-btn");
    if (borrowButton) borrowButton.addEventListener("click", async function () {
      if (!activeBook || addingBook) return notify("Save the book before borrowing it.", true);
      modal.classList.remove("active");
      try { await borrowBook(activeBook); } catch (error) { notify(error.message, true); }
    });
    const barcodeInput = document.getElementById("barcodeInput");
    const scanBarcodeButton = document.getElementById("scanBarcodeButton");
    const generateBarcodeButton = document.getElementById("generateBarcodeButton");
    const printBarcodeButton = document.getElementById("printBarcodeButton");
    if (scanBarcodeButton && barcodeInput) scanBarcodeButton.addEventListener("click", function () {
      barcodeInput.focus();
      notify("Scan the physical barcode, or type it manually and press Enter.");
    });
    if (barcodeInput) barcodeInput.addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      event.preventDefault();
      verifyScannedBarcode(barcodeInput);
    });
    if (generateBarcodeButton && barcodeInput) generateBarcodeButton.addEventListener("click", async function () {
      try {
        const result = await request("/books/generate-barcode", { method: "POST" });
        barcodeInput.value = result.barcode;
        updateBarcodePreview(result.barcode);
        notify("Unique barcode generated.");
      } catch (error) { notify(error.message, true); }
    });
    if (printBarcodeButton) printBarcodeButton.addEventListener("click", printBarcode);
    const coverInput = document.getElementById("bookCoverInput");
    const coverImage = document.getElementById("bookCoverImage");
    const coverFileName = document.getElementById("coverFileName");
    const saveCover = document.getElementById("saveBookCover");
    const resetCover = document.getElementById("resetBookCover");
    if (coverInput) coverInput.addEventListener("change", function () {
      const file = coverInput.files && coverInput.files[0];
      if (!file) return;
      if (coverPreviewUrl) URL.revokeObjectURL(coverPreviewUrl);
      coverPreviewUrl = URL.createObjectURL(file);
      coverImage.src = coverPreviewUrl;
      if (coverFileName) coverFileName.textContent = file.name;
    });
    if (saveCover) saveCover.addEventListener("click", async function () {
      const file = coverInput && coverInput.files && coverInput.files[0];
      if (!activeBook || !file) return notify("Choose an image file first.", true);
      const formData = new FormData();
      formData.append("cover", file);
      try {
        await request("/books/" + activeBook.id + "/cover", { method: "POST", body: formData });
        if (coverPreviewUrl) URL.revokeObjectURL(coverPreviewUrl);
        coverPreviewUrl = null;
        coverInput.value = "";
        if (coverFileName) coverFileName.textContent = "Image saved";
        await refreshBooks(document.querySelector(".search-box input")?.value || "");
        coverImage.src = "/api/books/" + activeBook.id + "/cover?updated=" + Date.now();
        notify("Book image updated.");
      } catch (error) { notify(error.message, true); }
    });
    if (resetCover) resetCover.addEventListener("click", async function () {
      if (!activeBook) return;
      try {
        await request("/books/" + activeBook.id + "/cover", { method: "DELETE" });
        if (coverPreviewUrl) URL.revokeObjectURL(coverPreviewUrl);
        coverPreviewUrl = null;
        coverInput.value = "";
        coverImage.src = "../img/ptc_logo.png";
        if (coverFileName) coverFileName.textContent = "Choose an image file";
        await refreshBooks(document.querySelector(".search-box input")?.value || "");
        notify("Book image reset to the default.");
      } catch (error) { notify(error.message, true); }
    });
    refreshBooks("").catch(function (error) { notify(error.message, true); });
  }

  function showPersonForm(person) {
    const dialog = document.createElement("dialog");
    dialog.className = "api-dialog people-dialog";
    const roles = ["Student", "Faculty", "Staff", "Other"];
    const roleOptions = roles.map(function (role) {
      return '<option value="' + role + '"' + ((person && person.role === role) ? " selected" : "") + ">" + role + "</option>";
    }).join("");
    const statusOptions = ["active", "inactive"].map(function (status) {
      const label = status === "active" ? "Active" : "Inactive";
      return '<option value="' + status + '"' + ((person && String(person.status).toLowerCase() === status) ? " selected" : "") + ">" + label + "</option>";
    }).join("");
    dialog.innerHTML = '<form><h2>' + (person ? "Edit People Information" : "Add People Information") +
      '</h2><div class="dialog-field"><label>School ID<input name="school_id" required maxlength="50" autocomplete="off" value="' + escapeHtml(person && person.school_id) + '"></label>' +
      (person ? "" : '<small class="person-lookup-status" aria-live="polite"></small>') + '</div>' +
      '<div class="dialog-field"><label>Full Name<input name="name" required maxlength="200" value="' + escapeHtml(person && person.name) + '"></label></div>' +
      '<div class="dialog-field"><label>Role<select name="role" required>' + roleOptions + '</select></label></div>' +
      '<div class="dialog-field"><label>Department<input name="department" maxlength="100" value="' + escapeHtml(person && person.department) + '"></label></div>' +
      '<div class="dialog-field"><label>Contact information<input name="contact" maxlength="80" value="' + escapeHtml(person && person.contact) + '"></label></div>' +
      '<div class="dialog-field"><label>Status<select name="status">' + statusOptions + '</select></label></div>' +
      '<div class="dialog-field"><label>Upload Face Image<input type="file" name="face_image" accept="image/png,image/jpeg,image/gif,image/webp"></label></div>' +
      (person && person.has_face_image ? '<p>Registered face image is stored securely in the main backend.</p>' : "") +
      '<div class="api-dialog-actions"><button type="button" data-cancel>Cancel</button><button type="submit">Save Information</button></div></form>';
    document.body.appendChild(dialog);
    dialog.addEventListener("close", function () { dialog.remove(); });
    dialog.querySelector("[data-cancel]").addEventListener("click", function () { dialog.close(); });
    // ---- Automatic information detection (Add mode only) ----
    // Typing a School ID looks it up in the database and fills in the saved details.
    // If the person already exists (for example, imported from Excel), saving updates that record.
    let matched = null;
    let lastLookedUpId = "";
    let lookupTimer = null;
    let lookupSeq = 0;
    const personForm = dialog.querySelector("form");
    const lookupStatus = dialog.querySelector(".person-lookup-status");
    const submitButton = personForm.querySelector('button[type="submit"]');
    const heading = personForm.querySelector("h2");

    function setLookupStatus(message, kind) {
      if (!lookupStatus) return;
      lookupStatus.textContent = message || "";
      lookupStatus.style.cssText = "font-size:12px;min-height:16px;color:" +
        (kind === "error" ? "#B3261E" : kind === "ok" ? "#1E7A3E" : "#5B7466");
    }
    function applyMatch(found) {
      matched = found;
      personForm.elements.name.value = found ? found.name : "";
      personForm.elements.role.value = found ? found.role : "Student";
      personForm.elements.department.value = found ? (found.department || "") : "";
      personForm.elements.contact.value = found ? (found.contact || "") : "";
      personForm.elements.status.value = found ? String(found.status).toLowerCase() : "active";
      heading.textContent = found ? "Update People Information" : "Add People Information";
      submitButton.textContent = found ? "Update Information" : "Save Information";
    }
    async function lookupSchoolId() {
      const typedId = personForm.elements.school_id.value.trim();
      const seq = ++lookupSeq;
      lastLookedUpId = typedId;
      if (!typedId) {
        if (matched) applyMatch(null);
        setLookupStatus("");
        return;
      }
      setLookupStatus("Looking up School ID...", "info");
      let result;
      try {
        result = await request("/people/lookup?school_id=" + encodeURIComponent(typedId));
      } catch (error) {
        if (seq === lookupSeq) setLookupStatus(error.message, "error");
        return;
      }
      if (seq !== lookupSeq) return;
      if (result.person) {
        applyMatch(result.person);
        personForm.elements.school_id.value = result.person.school_id;
        lastLookedUpId = result.person.school_id;
        setLookupStatus("Existing record found: " + result.person.name + ". Details filled in; saving will update this record." +
          (result.person.has_face_image ? " A face image is already uploaded; choosing a new one replaces it." : ""), "ok");
      } else {
        if (matched) applyMatch(null);
        setLookupStatus(result.system_account ? "This School ID belongs to a system account, not a person record."
                                              : "No saved record for this School ID. Enter the details to add a new person.",
                        result.system_account ? "error" : "info");
      }
    }
    if (!person) {
      const idField = personForm.elements.school_id;
      idField.addEventListener("input", function () {
        clearTimeout(lookupTimer);
        lookupTimer = setTimeout(lookupSchoolId, 400);
      });
      idField.addEventListener("blur", function () { clearTimeout(lookupTimer); lookupSchoolId(); });
      idField.addEventListener("keydown", function (event) {
        if (event.key !== "Enter") return;
        event.preventDefault();
        clearTimeout(lookupTimer);
        lookupSchoolId();
      });
    }

    dialog.querySelector("form").addEventListener("submit", async function (event) {
      event.preventDefault();
      const form = event.currentTarget;
      if (!person && form.elements.school_id.value.trim() !== lastLookedUpId) {
        await lookupSchoolId();
        if (matched) {
          notify("An existing record was found and loaded. Review the details, then save again.");
          return;
        }
      }
      const target = person || matched;
      const values = new FormData(form);
      const image = values.get("face_image");
      const payload = {
        school_id: values.get("school_id").trim(),
        name: values.get("name").trim(),
        role: values.get("role"),
        department: values.get("department").trim(),
        contact: values.get("contact").trim(),
        status: values.get("status")
      };
      try {
        const result = await request(target ? "/people/" + target.id : "/people", {
          method: target ? "PUT" : "POST", body: JSON.stringify(payload)
        });
        const personId = target ? target.id : result.id;
        let faceResult = null;
        let faceError = null;
        if (image && image.size) {
          const upload = new FormData();
          upload.append("face_image", image);
          try {
            faceResult = await request("/people/" + personId + "/face", { method: "POST", body: upload });
          } catch (error) { faceError = error; }
        }
        dialog.close();
        if (faceError) {
          notify("People information saved, but the face image was not registered: " + faceError.message, true);
        } else if (faceResult) {
          notify("People information saved. " + faceResult.message);
        } else {
          notify(result.message);
        }
        await loadManagedRecords("people", managedSearchText, managedRoleFilter);
      } catch (error) { notify(error.message, true); }
    });
    dialog.showModal();
  }

  function showImportResult(result) {
    const dialog = document.createElement("dialog");
    dialog.className = "api-dialog people-dialog import-result-dialog";
    const errors = (result.errors || []).map(function (item) {
      return "<li>Row " + escapeHtml(String(item.row)) + (item.school_id ? " (" + escapeHtml(item.school_id) + ")" : "") +
        ": " + escapeHtml(item.reason) + "</li>";
    }).join("");
    dialog.innerHTML = '<form method="dialog"><h2>Excel Import Complete</h2>' +
      '<ul class="import-summary">' +
      "<li><strong>" + result.added + "</strong> added</li>" +
      "<li><strong>" + result.updated + "</strong> updated</li>" +
      "<li><strong>" + result.unchanged + "</strong> unchanged</li>" +
      "<li><strong>" + result.skipped + "</strong> skipped</li></ul>" +
      (result.matched_columns && result.matched_columns.length
        ? '<h3 class="import-columns-title">Columns matched automatically' + (result.sheet ? " (sheet: " + escapeHtml(result.sheet) + ")" : "") + '</h3><ul class="import-columns">' +
          result.matched_columns.map(function (item) {
            return "<li><strong>" + escapeHtml(item.field) + "</strong> &larr; " + escapeHtml(item.column) + "</li>";
          }).join("") + "</ul>" : "") +
      (result.ignored_columns && result.ignored_columns.length
        ? '<p class="import-ignored">Ignored columns: ' + escapeHtml(result.ignored_columns.join(", ")) + "</p>" : "") +
      "<p>Face images are not imported. Upload them manually: enter the School ID in Add People Information (or use Edit) and choose the image.</p>" +
      (errors ? '<h3 class="import-errors-title">Rows that were skipped</h3><ul class="import-errors">' + errors +
        (result.more_errors ? "<li>...and " + result.more_errors + " more.</li>" : "") + "</ul>" : "") +
      '<div class="api-dialog-actions"><button type="submit">Close</button></div></form>';
    document.body.appendChild(dialog);
    dialog.addEventListener("close", function () { dialog.remove(); });
    dialog.showModal();
  }

  function showSystemUserForm(user) {
    const roles = ["MIS Admin", "Librarian"].map(function (role) { return { value: role, label: role }; });
    const statuses = ["active", "inactive"].map(function (status) {
      return { value: status, label: status === "active" ? "Active" : "Inactive" };
    });
    const values = user || {};
    showDialog(user ? "Edit System Account" : "Add System User", [
      { name: "school_id", label: "Account ID", value: values.school_id, required: true },
      { name: "name", label: "Full name", value: values.name, required: true },
      { name: "email", label: "Institutional email", type: "email", value: values.email, required: true },
      { name: "role", label: "System role", type: "select", options: roles, value: values.role || "Librarian", required: true },
      { name: "department", label: "Department", value: values.department },
      { name: "status", label: "Account status", type: "select", options: statuses, value: values.status || "active", required: true },
      { name: "password", label: user ? "New password (leave blank to keep current)" : "Password (minimum 8 characters)", type: "password", required: !user }
    ], "Only MIS Admin and Librarian accounts can access the web system.", user ? "Save Changes" : "Add System User", async function (data) {
      await request(user ? "/system-users/" + user.id : "/system-users", {
        method: user ? "PUT" : "POST", body: JSON.stringify(data)
      });
      notify(user ? "System account updated." : "System account added.");
      await loadManagedRecords("system", managedSearchText, managedRoleFilter);
    });
  }

  let managedView = "system";
  let managedSearchText = "";
  let managedRoleFilter = "";

  async function loadManagedRecords(view, searchText, role) {
    const params = new URLSearchParams();
    if (searchText) params.set("q", searchText);
    if (role) params.set("role", role);
    const result = await request("/" + (view === "system" ? "system-users" : "people") + "?" + params.toString());
    managedUsers = view === "system" ? result.users : result.people;
    const body = document.querySelector(".stage tbody");
    const table = document.querySelector(".stage table");
    if (!body || !table) return;
    const head = table.querySelector("thead tr");
    if (view === "system") {
      head.innerHTML = "<th>NAME</th><th>EMAIL</th><th>ROLE</th><th>ACCOUNT STATUS</th><th>CREATED</th><th>LAST LOGIN</th><th class=\"center\">ACTION</th>";
      body.innerHTML = managedUsers.length ? managedUsers.map(function (user) {
        const status = String(user.status || "").toLowerCase();
        const statusLabel = status === "active" ? "Active" : status === "inactive" ? "Inactive" : user.status;
        return '<tr data-user-id="' + user.id + '"><td class="bold">' + escapeHtml(user.name) +
          '</td><td>' + escapeHtml(user.email) + '</td><td>' + escapeHtml(user.role) + '</td><td>' + escapeHtml(statusLabel) +
          '</td><td>' + escapeHtml(user.created_at || "--") + '</td><td>' + escapeHtml(user.last_login_at || "--") +
          '</td><td class="center actions"><button class="btn-icon edit" title="Edit system account" aria-label="Edit system account" data-action="edit">Edit</button>' +
          (status === "active" ? '<button class="btn-icon delete" title="Deactivate system account" aria-label="Deactivate system account" data-action="deactivate">Deactivate</button>' : "") +
          (status === "inactive" ? '<button class="btn-icon delete" title="Permanently delete system account" aria-label="Permanently delete system account" data-action="delete">Delete Permanently</button>' : "") +
          '</td></tr>';
      }).join("") : '<tr><td colspan="7">No system accounts found.</td></tr>';
    } else {
      head.innerHTML = "<th>SCHOOL ID</th><th>NAME</th><th>ROLE</th><th>DEPARTMENT</th><th>CONTACT</th><th>STATUS</th><th>FACE RECOGNITION</th><th class=\"center\">ACTION</th>";
      body.innerHTML = managedUsers.length ? managedUsers.map(function (person) {
        const status = String(person.status || "").toLowerCase();
        const statusLabel = status === "active" ? "Active" : status === "inactive" ? "Inactive" : person.status;
        return '<tr data-user-id="' + person.id + '"><td class="bold">' + escapeHtml(person.school_id) +
          '</td><td class="bold">' + escapeHtml(person.name) + '</td><td>' + escapeHtml(person.role) +
          '</td><td>' + escapeHtml(person.department) + '</td><td>' + escapeHtml(person.contact) + '</td><td>' + escapeHtml(statusLabel) +
          '</td><td>' + (person.face_registered ? "Registered" : (person.has_face_image ? "Pending" : "Not registered")) +
          '</td><td class="center actions"><button class="btn-icon edit" title="Edit people information" aria-label="Edit people information" data-action="edit">Edit</button>' +
          (status === "active" ? '<button class="btn-icon delete" title="Deactivate person" aria-label="Deactivate person" data-action="deactivate">Deactivate</button>' : "") +
          (status === "inactive" ? '<button class="btn-icon delete" title="Permanently delete people information" aria-label="Permanently delete people information" data-action="delete">Delete Permanently</button>' : "") +
          '</td></tr>';
      }).join("") : '<tr><td colspan="8">No people information found.</td></tr>';
    }
  }

  function bindUsersPage() {
    const tableBody = document.querySelector(".stage tbody");
    if (!tableBody) return;
    const search = document.getElementById("searchInput");
    const add = document.querySelector(".btn-add-user");
    const systemTab = document.getElementById("viewSystemUsers");
    const peopleTab = document.getElementById("viewPeopleInformation");
    const roleFilter = document.getElementById("systemRoleFilter");
    const importButton = document.getElementById("importPeopleButton");
    const importFile = document.getElementById("importPeopleFile");
    const importHint = document.getElementById("importPeopleHint");
    function setView(view) {
      managedView = view;
      managedRoleFilter = view === "system" && roleFilter ? roleFilter.value : "";
      managedSearchText = search ? search.value : "";
      if (systemTab) {
        systemTab.classList.toggle("active", view === "system");
        systemTab.setAttribute("aria-selected", String(view === "system"));
      }
      if (peopleTab) {
        peopleTab.classList.toggle("active", view === "people");
        peopleTab.setAttribute("aria-selected", String(view === "people"));
      }
      if (roleFilter) roleFilter.hidden = view !== "system";
      if (importButton) importButton.hidden = view !== "people";
      if (importHint) importHint.hidden = view !== "people";
      if (add) add.textContent = view === "system" ? "ADD SYSTEM USER" : "ADD PEOPLE INFORMATION";
      loadManagedRecords(view, managedSearchText, roleFilter ? roleFilter.value : "").catch(function (error) {
        notify(error.message, true);
      });
    }
    if (systemTab) systemTab.addEventListener("click", function () { setView("system"); });
    if (peopleTab) peopleTab.addEventListener("click", function () { setView("people"); });
    if (roleFilter) roleFilter.addEventListener("change", function () {
      managedRoleFilter = roleFilter.value;
      loadManagedRecords("system", managedSearchText, managedRoleFilter).catch(function (error) {
        notify(error.message, true);
      });
    });
    if (search) search.addEventListener("input", function () {
      managedSearchText = search.value;
      loadManagedRecords(managedView, managedSearchText, managedRoleFilter).catch(function (error) {
        notify(error.message, true);
      });
    });
    if (importButton && importFile) {
      importButton.addEventListener("click", function () { importFile.click(); });
      importFile.addEventListener("change", async function () {
        const file = importFile.files && importFile.files[0];
        importFile.value = "";
        if (!file) return;
        if (!/\.(xlsx|xls)$/i.test(file.name)) {
          notify("Choose an Excel file (.xlsx or .xls).", true);
          return;
        }
        const label = importButton.innerHTML;
        importButton.disabled = true;
        importButton.textContent = "IMPORTING...";
        try {
          const upload = new FormData();
          upload.append("file", file);
          const result = await request("/people/import", { method: "POST", body: upload });
          await loadManagedRecords("people", managedSearchText, managedRoleFilter);
          showImportResult(result);
        } catch (error) {
          notify(error.message, true);
        } finally {
          importButton.disabled = false;
          importButton.innerHTML = label;
        }
      });
    }
    if (add) add.addEventListener("click", function () {
      if (managedView === "system") showSystemUserForm(null);
      else showPersonForm(null);
    });
    tableBody.addEventListener("click", function (event) {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const row = button.closest("tr[data-user-id]");
      const record = managedUsers.find(function (item) { return item.id === Number(row.dataset.userId); });
      if (!record) return;
      if (button.dataset.action === "edit") {
        if (managedView === "system") showSystemUserForm(record);
        else showPersonForm(record);
        return;
      }
      const view = managedView;
      const isSystemUser = view === "system";
      if (button.dataset.action === "deactivate") {
        showDialog(isSystemUser ? "Deactivate System Account" : "Deactivate Person", [],
          record.name + (isSystemUser
            ? " will no longer be able to sign in."
            : " will no longer be able to enter the library."), "Deactivate", async function () {
            await request((isSystemUser ? "/system-users/" : "/people/") + record.id, { method: "DELETE" });
            notify(isSystemUser ? "System account deactivated." : "Person deactivated and attendance closed.");
            await loadManagedRecords(view, managedSearchText, managedRoleFilter);
          });
        return;
      }
      if (button.dataset.action === "delete") {
        if (String(record.status || "").toLowerCase() !== "inactive") {
          notify("Only inactive users can be permanently deleted.", true);
          return;
        }
        showDialog(isSystemUser ? "Permanently Delete System Account" : "Permanently Delete People Information", [],
          "Are you sure you want to permanently delete this inactive user? Their account and personal information will be deleted, but their historical records will be preserved. This action cannot be undone.",
          "Confirm Delete", async function () {
            await request((isSystemUser ? "/system-users/" : "/people/") + record.id + "/permanent", { method: "DELETE" });
            notify(isSystemUser ? "System account permanently deleted; history was preserved." : "People information permanently deleted; history was preserved.");
            await loadManagedRecords(view, managedSearchText, managedRoleFilter);
          });
      }
    });
    setView("system");
  }

  function bindReportsPage() {
    const table = document.querySelector(".main table");
    const tbody = table && table.querySelector("tbody");
    if (!table || !tbody) return;
    const dateInputs = document.querySelectorAll(".filter-card input");
    if (dateInputs.length >= 2) {
      dateInputs[0].value = "";
      dateInputs[0].type = "date";
      dateInputs[0].id = "dateFrom";
      dateInputs[1].value = "";
      dateInputs[1].type = "date";
      dateInputs[1].id = "dateTo";
    }
    const filterGrid = document.querySelector(".filter-grid");
    const extraFilters = [
      { id: "attendanceSearch", label: "Search by school ID or name", type: "search", placeholder: "School ID or name" },
      { id: "attendanceRole", label: "Role", type: "select", options: ["Student", "Faculty", "Staff", "Other"] },
      { id: "attendanceDepartment", label: "Department", type: "text", placeholder: "Department or course" },
      { id: "attendanceEvent", label: "Attendance records", type: "select", options: ["all", "time_in", "time_out"], labels: ["All sessions", "Time-in", "Time-out"] }
    ];
    extraFilters.forEach(function (filter) {
      if (document.getElementById(filter.id)) return;
      const field = document.createElement("div");
      field.className = "field filter-field";
      const label = document.createElement("label");
      label.htmlFor = filter.id;
      label.textContent = filter.label;
      let control;
      if (filter.type === "select") {
        control = document.createElement("select");
        const all = document.createElement("option");
        all.value = "";
        all.textContent = filter.id === "attendanceEvent" ? "All sessions" : "All roles";
        control.appendChild(all);
        filter.options.forEach(function (value, index) {
          const option = document.createElement("option");
          option.value = value === "all" ? "all" : value;
          option.textContent = filter.labels ? filter.labels[index] : value;
          control.appendChild(option);
        });
        if (filter.id === "attendanceEvent") control.value = "all";
      } else {
        control = document.createElement("input");
        control.type = filter.type;
        control.placeholder = filter.placeholder;
      }
      control.id = filter.id;
      field.append(label, control);
      if (filterGrid) filterGrid.appendChild(field);
    });
    const canDeleteAttendance = currentUser && currentUser.role === "MIS Admin";
    table.querySelector("thead").innerHTML = "<tr><th>SCHOOL ID</th><th>NAME</th><th>ROLE</th><th>DEPARTMENT</th><th>DATE</th><th>TIME-IN</th><th>TIME-OUT</th><th>STATUS</th>" +
      (canDeleteAttendance ? "<th class=\"center\">ACTION</th>" : "") + "</tr>";
    let reportRows = [];
    async function load() {
      const parameters = new URLSearchParams();
      const from = document.getElementById("dateFrom");
      const to = document.getElementById("dateTo");
      const search = document.getElementById("attendanceSearch");
      const role = document.getElementById("attendanceRole");
      const department = document.getElementById("attendanceDepartment");
      const eventType = document.getElementById("attendanceEvent");
      if (from && from.value) parameters.set("from", from.value);
      if (to && to.value) parameters.set("to", to.value);
      if (search && search.value.trim()) parameters.set("q", search.value.trim());
      if (role && role.value) parameters.set("role", role.value);
      if (department && department.value.trim()) parameters.set("department", department.value.trim());
      if (eventType && eventType.value) parameters.set("event", eventType.value);
      const result = await request("/reports/attendance?" + parameters.toString());
      reportRows = result.records;
      tbody.innerHTML = reportRows.length ? reportRows.map(function (item) {
        return "<tr><td>" + escapeHtml(item.school_id) + "</td><td>" + escapeHtml(item.name) +
          "</td><td>" + escapeHtml(item.role) + "</td><td>" + escapeHtml(item.department) +
          "</td><td>" + escapeHtml(item.attendance_date) + "</td><td>" + escapeHtml(item.time_in) +
          "</td><td>" + escapeHtml(item.time_out || "--") + "</td><td>" + escapeHtml(item.status) + "</td>" +
          (canDeleteAttendance ? '<td class="center actions"><button type="button" class="btn-icon delete" title="Delete attendance history record" aria-label="Delete attendance history record" data-action="delete-attendance" data-record-id="' +
            item.id + '">Delete</button></td>' : "") + "</tr>";
      }).join("") : '<tr><td colspan="' + (canDeleteAttendance ? "9" : "8") + '">No attendance records in this date range.</td></tr>';
    }
    if (canDeleteAttendance) {
      tbody.addEventListener("click", function (event) {
        const button = event.target.closest("button[data-action='delete-attendance']");
        if (!button) return;
        const record = reportRows.find(function (item) { return item.id === Number(button.dataset.recordId); });
        if (!record) return;
        showDialog("Delete Attendance History", [],
          "Delete the attendance record for " + record.name + " (" + record.school_id + ") on " +
            record.attendance_date + " at " + record.time_in + "? This action cannot be undone.",
          "Confirm Delete", async function () {
            await request("/attendance/" + record.id, { method: "DELETE" });
            notify("Attendance history record deleted.");
            await load();
          });
      });
    }
    [document.getElementById("dateFrom"), document.getElementById("dateTo"), document.getElementById("attendanceSearch"),
      document.getElementById("attendanceRole"), document.getElementById("attendanceDepartment"), document.getElementById("attendanceEvent")]
      .filter(Boolean).forEach(function (input) {
        input.addEventListener(input.type === "search" || input.type === "text" ? "input" : "change", function () {
          load().catch(function (error) { notify(error.message, true); });
        });
      });
    const generate = document.querySelector(".btn-generate, .gen-btn");
    if (generate) generate.addEventListener("click", async function () {
      const reportWindow = window.open("", "_blank");
      if (!reportWindow) {
        notify("Allow pop-ups for this site to generate the PDF report.", true);
        return;
      }
      try {
        await load();
        const from = document.getElementById("dateFrom");
        const to = document.getElementById("dateTo");
        const search = document.getElementById("attendanceSearch");
        const role = document.getElementById("attendanceRole");
        const department = document.getElementById("attendanceDepartment");
        const eventType = document.getElementById("attendanceEvent");
        const filters = [
          from && from.value ? "From: " + from.value : "",
          to && to.value ? "To: " + to.value : "",
          search && search.value.trim() ? "Search: " + search.value.trim() : "",
          role && role.value ? "Role: " + role.value : "",
          department && department.value.trim() ? "Department: " + department.value.trim() : "",
          eventType && eventType.value && eventType.value !== "all"
            ? "Record type: " + eventType.options[eventType.selectedIndex].text
            : ""
        ].filter(Boolean);
        const generatedAt = new Date().toLocaleString("en-PH", { dateStyle: "medium", timeStyle: "short" });
        const tableRows = reportRows.map(function (item) {
          return "<tr><td>" + escapeHtml(item.school_id) + "</td><td>" + escapeHtml(item.name) +
            "</td><td>" + escapeHtml(item.role) + "</td><td>" + escapeHtml(item.department) +
            "</td><td>" + escapeHtml(item.attendance_date) + "</td><td>" + escapeHtml(item.time_in) +
            "</td><td>" + escapeHtml(item.time_out || "--") + "</td><td><span class=\"status\">" +
            escapeHtml(item.status) + "</span></td></tr>";
        });
        const logoUrl = new URL("../img/ptc_logo.png", window.location.href).href;
        const reportMarkup = '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">' +
          '<meta name="viewport" content="width=device-width, initial-scale=1"><title>PTC Attendance Report</title>' +
          '<style>' +
          '@page{size:landscape;margin:13mm}*{box-sizing:border-box}body{margin:0;background:#eef3ef;color:#183b2b;font:10px/1.45 Arial,sans-serif}' +
          '.report{max-width:1200px;margin:24px auto;padding:30px;background:#fff;box-shadow:0 8px 28px #173e2920}' +
          '.masthead{display:flex;align-items:center;gap:14px;padding:0 0 18px;border-bottom:2px solid #d8e6dc}' +
          '.logo{width:54px;height:54px;object-fit:contain}.school{color:#587064;font-size:9px;font-weight:700;letter-spacing:1.6px;text-transform:uppercase}' +
          'h1{margin:4px 0 0;font-size:24px;line-height:1.15;letter-spacing:-.5px}.subtitle{margin:5px 0 0;color:#66786d;font-size:11px}' +
          '.summary{display:flex;gap:12px;margin:20px 0}.summary-card{flex:1;padding:12px 14px;border:1px solid #dce8df;border-radius:8px;background:#f6f9f6}' +
          '.summary-label{color:#63766a;font-size:8px;font-weight:700;letter-spacing:1px;text-transform:uppercase}.summary-value{margin-top:4px;font-size:14px;font-weight:700}' +
          '.filters{margin:0 0 15px;padding:10px 12px;border-left:3px solid #38734f;background:#f3f7f4;color:#43594b;font-size:9px}' +
          '.filters strong{margin-right:5px;color:#183b2b}.table-wrap{width:100%;overflow:visible}table{width:100%;border-collapse:collapse;table-layout:fixed}' +
          'thead{display:table-header-group}th{padding:9px 7px;background:#204a34;color:#fff;text-align:left;font-size:8px;letter-spacing:.45px;text-transform:uppercase}' +
          'td{padding:8px 7px;border-bottom:1px solid #e2eae4;color:#294536;vertical-align:top;overflow-wrap:anywhere;font-size:8.5px}' +
          'tbody tr:nth-child(even){background:#f5f8f5}tr{break-inside:avoid}.status{display:inline-block;padding:2px 6px;border-radius:10px;background:#e5f2e8;color:#2d6842;font-size:7px;font-weight:700;text-transform:capitalize}' +
          '.empty{text-align:center;color:#718176;padding:24px}.footer{display:flex;justify-content:space-between;gap:20px;margin-top:18px;padding-top:9px;border-top:1px solid #dce6de;color:#76847a;font-size:8px}' +
          '@media print{body{background:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact}.report{max-width:none;margin:0;padding:0;box-shadow:none}.summary-card{break-inside:avoid}}' +
          '</style></head><body><main class="report"><header class="masthead"><img class="logo" src="' + escapeHtml(logoUrl) +
          '" alt=""><div><div class="school">Pateros Technological College · Smart Library System</div><h1>Attendance Report</h1>' +
          '<p class="subtitle">Official time-in and time-out records</p></div></header><section class="summary">' +
          '<div class="summary-card"><div class="summary-label">Records included</div><div class="summary-value">' + reportRows.length +
          '</div></div><div class="summary-card"><div class="summary-label">Date range</div><div class="summary-value">' +
          escapeHtml(from && from.value ? from.value : "All dates") + " – " + escapeHtml(to && to.value ? to.value : "Present") +
          '</div></div><div class="summary-card"><div class="summary-label">Prepared</div><div class="summary-value">' +
          escapeHtml(generatedAt) + '</div></div></section><div class="filters"><strong>Applied filters:</strong>' +
          escapeHtml(filters.length ? filters.join(" · ") : "All available records") +
          '</div><div class="table-wrap"><table><thead><tr><th style="width:10%">School ID</th><th style="width:16%">Name</th>' +
          '<th style="width:9%">Role</th><th style="width:15%">Department</th><th style="width:10%">Date</th>' +
          '<th style="width:12%">Time-in</th><th style="width:12%">Time-out</th><th style="width:10%">Status</th></tr></thead><tbody>' +
          (tableRows.length ? tableRows.join("") : '<tr><td class="empty" colspan="8">No attendance records match the selected filters.</td></tr>') +
          '</tbody></table></div><footer class="footer"><span>Generated from the Smart Library System</span><span>For authorized institutional use</span></footer></main></body></html>';
        reportWindow.document.open();
        reportWindow.document.write(reportMarkup);
        reportWindow.document.close();
        reportWindow.document.title = "PTC-Attendance-Report";
        window.setTimeout(function () {
          if (!reportWindow.closed) {
            reportWindow.focus();
            reportWindow.print();
          }
        }, 300);
      } catch (error) {
        reportWindow.close();
        notify(error.message, true);
      }
    });
    load().catch(function (error) { notify(error.message, true); });
  }

  function bindAttendancePage() {
    const table = document.querySelector(".table-container table, .stage table");
    const search = document.querySelector(".search-box input");
    if (!table) return;
    const tbody = table.querySelector("tbody");
    table.querySelector("thead").innerHTML = "<tr><th>SCHOOL ID</th><th>NAME</th><th>ROLE</th><th>DEPARTMENT</th><th>DATE</th><th>TIME-IN</th><th>TIME-OUT</th><th>STATUS</th></tr>";
    async function load() {
      const query = search && search.value.trim() ? "?q=" + encodeURIComponent(search.value.trim()) : "";
      const result = await request("/attendance" + query);
      tbody.innerHTML = result.records.length ? result.records.map(function (item) {
        return "<tr><td>" + escapeHtml(item.school_id) + "</td><td>" + escapeHtml(item.name) +
          "</td><td>" + escapeHtml(item.role) + "</td><td>" + escapeHtml(item.department) +
          "</td><td>" + escapeHtml(item.attendance_date) + "</td><td><span class=\"time-badge\">" +
          escapeHtml(item.time_in) + "</span></td><td>" + (item.time_out ? '<span class="time-badge red">' +
          escapeHtml(item.time_out) + "</span>" : "--") + "</td><td>" + escapeHtml(item.status) + "</td></tr>";
      }).join("") : '<tr><td colspan="8">No attendance records found.</td></tr>';
    }
    if (search) search.addEventListener("input", function () { load().catch(function (error) { notify(error.message, true); }); });
    load().catch(function (error) { notify(error.message, true); });
  }

  async function initializeRolePage() {
    const path = window.location.pathname;
    const expectedRole = path.startsWith("/MIS_ADMIN/") ? "MIS Admin" : "Librarian";
    const result = await request("/auth/me");
    currentUser = result.user;
    if (currentUser.role !== expectedRole) {
      window.location.href = currentUser.role === "MIS Admin" ? "/MIS_ADMIN/dashboard.html" : "/LIBRARIAN/dashboard.html";
      return;
    }
    updateAccountName();
    bindLogout();
    const page = path.split("/").pop();
    if (page === "dashboard.html") {
      const refresh = async function () { renderDashboard(await request("/dashboard")); };
      await refresh();
      if (currentUser.role === "MIS Admin") await bindRecoveryRequests();
      window.setInterval(function () { refresh().catch(function (error) { notify(error.message, true); }); }, 15000);
    } else if (page === "time_in_out.html") {
      bindAttendancePage();
    } else if (page === "book_transactions.html") {
      bindBooksPage();
    } else if (page === "reports.html") {
      bindReportsPage();
    } else if (page === "users_management.html") {
      bindUsersPage();
    }
  }

  window.login = login;
  window.togglePassword = togglePassword;
  document.addEventListener("DOMContentLoaded", function () {
    const loginForm = document.querySelector("form");
    if (document.getElementById("email") && loginForm) {
      if (window.location.pathname === "/LogIn.html") {
        loginForm.addEventListener("submit", login);
      }
    }
    if (window.location.pathname === "/EmailVerification.html") {
      const emailField = document.getElementById("email");
      if (emailField && loginForm) {
        loginForm.addEventListener("submit", function (event) {
          event.preventDefault();
          const email = emailField.value.trim();
          if (!emailField.checkValidity()) {
            emailField.reportValidity();
            return;
          }
          request("/auth/recovery/request", {
            method: "POST",
            body: JSON.stringify({ email: email })
          }).then(function (result) {
            notify(result.message, false);
            sessionStorage.setItem("recovery_request_key", result.request_key);
            window.location.href = "/VerificationCode.html";
          }).catch(function (error) { notify(error.message, true); });
        });
      }
    }
    if (window.location.pathname === "/VerificationCode.html") {
      const codeInputs = Array.from(document.querySelectorAll(".code-container input"));
      const form = document.querySelector("form");
      const requestKey = sessionStorage.getItem("recovery_request_key") || "";
      const status = document.getElementById("recovery-status");
      if (!requestKey) {
        if (status) status.textContent = "Submit a recovery request first.";
      } else {
        const updateStatus = async function () {
          const result = await request("/auth/recovery/status", {
            method: "POST",
            body: JSON.stringify({ request_key: requestKey })
          });
          if (status) {
            const messages = {
              pending: "If eligible, your request is awaiting MIS Admin approval.",
              approved: "Approved. Enter the six-digit code provided to you by the MIS Admin.",
              verified: "Verified. Continue to set your new password.",
              rejected: "Your recovery request was rejected. Submit a new request if needed.",
              expired: "The request or code expired. Return to Forgot Password to start again.",
              completed: "This recovery request has already been completed."
            };
            status.textContent = messages[result.status] || "Recovery status: " + result.status + ".";
          }
          const approved = result.status === "approved";
          codeInputs.forEach(function (input) { input.disabled = !approved; });
          const submit = form && form.querySelector("button[type='submit']");
          if (submit) submit.disabled = !approved;
          if (result.expires_at) {
            const remaining = Math.max(0, Math.floor((Date.parse(result.expires_at) - Date.now()) / 1000));
            if (status && remaining > 0) {
              status.textContent += " Code expires in " +
                Math.floor(remaining / 60) + ":" + String(remaining % 60).padStart(2, "0") + ".";
            }
          }
          return result;
        };
        updateStatus().catch(function (error) { if (status) status.textContent = error.message; });
        window.setInterval(function () {
          updateStatus().catch(function (error) { if (status) status.textContent = error.message; });
        }, 5000);
      }
      if (form && codeInputs.length) {
        form.addEventListener("submit", function (event) {
          event.preventDefault();
          const code = codeInputs.map(function (input) { return input.value.trim(); }).join("");
          if (!requestKey || !/^\d{6}$/.test(code)) {
            notify("Enter the full six-digit recovery code.", true);
            return;
          }
          request("/auth/recovery/verify", {
            method: "POST",
            body: JSON.stringify({ request_key: requestKey, code: code })
          }).then(function (result) {
            notify(result.message, false);
            sessionStorage.setItem("recovery_reset_token", result.reset_token);
            window.location.href = "/ChangePassword.html";
          }).catch(function (error) { notify(error.message, true); });
        });
        codeInputs.forEach(function (input, index) {
          input.addEventListener("input", function () {
            input.value = input.value.replace(/\D/g, "").slice(0, 1);
            if (input.value && index < codeInputs.length - 1) {
              codeInputs[index + 1].focus();
            }
          });
          input.addEventListener("keydown", function (event) {
            if (event.key === "Backspace" && !input.value && index > 0) {
              codeInputs[index - 1].focus();
            }
          });
        });
      }
    }
    if (window.location.pathname === "/ChangePassword.html") {
      const form = document.querySelector("form");
      const requestKey = sessionStorage.getItem("recovery_request_key") || "";
      const resetToken = sessionStorage.getItem("recovery_reset_token") || "";
      if (!requestKey || !resetToken) {
        window.location.replace("/VerificationCode.html");
        return;
      }
      if (form) {
        form.addEventListener("submit", function (event) {
          event.preventDefault();
          const requestKey = sessionStorage.getItem("recovery_request_key") || "";
          const resetToken = sessionStorage.getItem("recovery_reset_token") || "";
          const newPassword = document.getElementById("new-password").value;
          const confirmPassword = document.getElementById("confirm-password").value;
          if (!requestKey || !resetToken) {
            notify("Verify the recovery code before changing the password.", true);
            return;
          }
          requestPasswordReset(requestKey, resetToken, newPassword, confirmPassword).catch(function (error) { notify(error.message, true); });
        });
      }
    }
    if (window.location.pathname.startsWith("/LIBRARIAN/") || window.location.pathname.startsWith("/MIS_ADMIN/")) {
      addInteractiveStyles();
      clearExampleRows(window.location.pathname);
      initializeRolePage().catch(function (error) { notify(error.message, true); });
    }
  });
}());
