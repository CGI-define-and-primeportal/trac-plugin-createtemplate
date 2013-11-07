$(document).ready(function(){

  $("#createtemplateform").validate({
    errorClass: 'ui-state-error'
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