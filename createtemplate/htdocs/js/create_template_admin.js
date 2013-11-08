$(document).ready(function(){

  $("#createtemplateform").validate({
    errorClass: 'ui-state-error'
  });

  // check that the name entered into the template name field is not already used
  $("[name='template-name']").blur(function() {
    if ($.inArray($(this).val(), used_names) != -1) {
      $(this).addClass("ui-state-error");
      // add html
      $(this).parent().append( '<label for="template-name" class="ui-state-error"> \
                               This template name has already been used.</label>' );
    }
  });

  $("#template-info-toggle > li a").on("click", function() {
    $(this).parent().next().slideToggle('fast');
  });

  $("#see-more-template-info").on("click", function () {
    $("#template_info").slideToggle('fast');
  });

  $('#name-question').click(function() {
    $('#template-name-dialog').dialog({
      title: 'More Information - Template Name',
      width: 400,
      modal: true,
      buttons: {
        'Close': function() {
          $(this).dialog('close');
        }
      }
    });
  });

  $('#description-question').click(function() {
    $('#template-description-dialog').dialog({
      title: 'More Information - Template Description',
      width: 400,
      modal: true,
      buttons: {
        'Close': function() {
          $(this).dialog('close');
        }
      }
    });
  });

  $('#component-question').click(function() {
    $('#template-component-dialog').dialog({
      title: 'More Information - Template Options',
      width: 400,
      modal: true,
      buttons: {
        'Close': function() {
          $(this).dialog('close');
        }
      }
    });
  });

});