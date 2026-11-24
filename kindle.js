(function () {
  var inbox = document.getElementById("inbox");
  var previous = null;

  function refreshInbox() {
    var request = new XMLHttpRequest();
    request.open("GET", "/kindle/inbox", true);
    request.onreadystatechange = function () {
      if (request.readyState !== 4) return;
      if (request.status === 200 && request.responseText !== previous) {
        if (request.responseText !== inbox.innerHTML) inbox.innerHTML = request.responseText;
        previous = request.responseText;
      }
    };
    request.send(null);
  }

  if (inbox && window.XMLHttpRequest) {
    window.setInterval(refreshInbox, 8000);
  }
}());
