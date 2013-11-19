var form = {

  dialogs: ["description", "component"],

  init: function() {
    form.$container = $("#create-template-form");
    form.$name = $("#template-name");
    form.draw_dialogs();
    form.init_validation();
    $("#template-components").select2({
      width: "off"
    });
    form.events();
  },

  draw_dialogs: function() {
    $.each(form.dialogs, function(__, name) {
      var $handle = $("#" + name + "-handle"),
          $dialog = $("#" + name + "-dialog");

      $dialog.dialog({
        modal: true,
        autoOpen: false,
        buttons: {
          Close: function() {
            $dialog.dialog("close");
          }
        }
      });

      $handle.on("click", function() {
        $dialog.dialog("open");
      });
    });
  },

  init_validation: function() {
    // Add validation method to check unique template name
    $.validator.addMethod("unique", function(val) {
      return $.inArray(val, window.usedNames) == -1;
    }, "This template name has already been used");

    form.$container.validate({
      errorClass: "ui-state-error",
      rules: {
        "template-name": { unique: true }
      }
    });
  },

  toggle_more_info: function() {
    $("#template-info").slideToggle();
  },

  toggle_components: function() {
    $(this).next().slideToggle();
  },

  events: function() {
    $("#template-info-toggle").on("click", form.toggle_more_info);
    $("a", "#template-component-toggle").on("click", form.toggle_components);
  }
};

$(document).ready(form.init);