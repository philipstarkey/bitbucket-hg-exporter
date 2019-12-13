'use strict';

// Register `pullrequestDetails` component, along with its associated controller and template
angular.
  module('pullrequestDetails').
  component('pullrequestDetails', {
    templateUrl: 'ng/pullrequest-details/pullrequest-details.template.html',
    controller: ['$http', '$routeParams', '$sce', '$rootScope', '$location', '$timeout', '$anchorScroll', function PullrequestDetailsController($http, $routeParams, $sce, $rootScope, $location, $timeout, $anchorScroll) {
        var self = this;

        //pagination info
        self.prId = $routeParams.prId;
        self.commentPage = $routeParams.pageId;
        self.project_slug = $routeParams.owner + '/' + $routeParams.project;
        self.mainHtml = "";
      
      
        $http.get($rootScope.projects[self.project_slug]['project_path']+'pullrequests/'+self.prId+'.json').then(function(response) {
            self.pr = response.data;
            self.mainHtml = $sce.trustAsHtml(self.pr['rendered']['description']['html']);
        });

        $http.get($rootScope.projects[self.project_slug]['project_path']+'pullrequests/'+self.prId+'/comments_page='+self.commentPage+'.json').then(function(response) {
          self.comments = response.data;
          angular.forEach(self.comments['values'], function(value, index){
            self.comments['values'][index]['content']['html'] = $sce.trustAsHtml(self.comments['values'][index]['content']['html']);

          });

          // Now that the comments are (about to be) loaded
          // scroll to the comment specified in the hash if there is one
          $timeout(function(){$anchorScroll($location.hash());});
      });
        
    }]
  });