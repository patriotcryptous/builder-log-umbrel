/* Photo annotations: click the photo to drop a numbered pin, then label it.
   Pins are kept in a hidden field and saved with the rest of the form, so the
   page needs no inline script and no API calls. */
(function () {
  "use strict";

  var wrap = document.querySelector("[data-annotate]");
  var field = document.getElementById("pins-field");
  var list = document.getElementById("pin-list");
  var toggle = document.getElementById("pin-toggle");
  var hint = document.getElementById("pin-hint");
  if (!wrap || !field || !list || !toggle) {
    return;
  }

  var pins = [];
  try {
    var parsed = JSON.parse(field.value || "[]");
    if (Array.isArray(parsed)) {
      pins = parsed;
    }
  } catch (err) {
    pins = [];
  }

  var editing = false;

  function save() {
    pins.forEach(function (pin, index) {
      pin.number = index + 1;
    });
    field.value = JSON.stringify(pins);
  }

  function drawPins() {
    var old = wrap.querySelectorAll(".pin");
    for (var i = 0; i < old.length; i++) {
      old[i].remove();
    }
    pins.forEach(function (pin, index) {
      var dot = document.createElement("span");
      dot.className = "pin";
      dot.textContent = String(index + 1);
      dot.style.setProperty("left", pin.x + "%");
      dot.style.setProperty("top", pin.y + "%");
      wrap.appendChild(dot);
    });
  }

  function drawList() {
    list.textContent = "";
    if (!pins.length) {
      var note = document.createElement("li");
      note.className = "muted small";
      note.textContent = editing
        ? "Click a spot on the photo to add the first pin."
        : "No pins yet.";
      list.appendChild(note);
      return;
    }
    pins.forEach(function (pin, index) {
      var row = document.createElement("li");

      var num = document.createElement("span");
      num.className = "pin-num";
      num.textContent = String(index + 1);
      row.appendChild(num);

      var input = document.createElement("input");
      input.type = "text";
      input.value = pin.label || "";
      input.maxLength = 200;
      input.setAttribute("aria-label", "Label for pin " + (index + 1));
      input.placeholder = "What is this?";
      input.addEventListener("input", function () {
        pins[index].label = input.value;
        save();
      });
      row.appendChild(input);

      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-sm btn-danger";
      remove.textContent = "Remove";
      remove.addEventListener("click", function () {
        pins.splice(index, 1);
        save();
        drawPins();
        drawList();
      });
      row.appendChild(remove);

      list.appendChild(row);
    });
  }

  function setEditing(on) {
    editing = on;
    wrap.classList.toggle("editing", on);
    toggle.textContent = on ? "Done placing pins" : "Place pins";
    toggle.classList.toggle("btn-primary", on);
    if (hint) {
      hint.hidden = !on;
    }
    drawList();
  }

  wrap.addEventListener("click", function (event) {
    if (!editing) {
      return;
    }
    var image = wrap.querySelector("img");
    if (!image) {
      return;
    }
    var box = image.getBoundingClientRect();
    if (!box.width || !box.height) {
      return;
    }
    var x = ((event.clientX - box.left) / box.width) * 100;
    var y = ((event.clientY - box.top) / box.height) * 100;
    if (x < 0 || x > 100 || y < 0 || y > 100) {
      return;
    }
    pins.push({
      number: pins.length + 1,
      x: Math.round(x * 100) / 100,
      y: Math.round(y * 100) / 100,
      label: ""
    });
    save();
    drawPins();
    drawList();
  });

  toggle.addEventListener("click", function () {
    setEditing(!editing);
  });

  save();
  drawPins();
  setEditing(false);
})();
